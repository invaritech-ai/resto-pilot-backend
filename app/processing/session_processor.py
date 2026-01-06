from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

from sqlalchemy import select

from app.ai.chat_memory import (
    load_chat_history_messages,
    load_chat_memory_summary,
    update_chat_memory_summary,
)
from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.capability_gate import classify_capability, inventory_outlet_list_refusal, is_outlet_list_request
from app.ai.session_reply import generate_session_reply_with_metrics
from app.ai.topic_gate import classify_on_topic
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_chat_memory import TelegramChatMemory
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.bot_api import send_message
from app.workers.telemetry import (
    get_or_create_chat_state,
    record_llm_call,
    record_outgoing_message,
    schedule_openrouter_cost_backfill,
    set_chat_off_topic,
    set_chat_on_topic,
)
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)

OFF_TOPIC_REDIRECT_TEXT = (
    "Only inventory updates are supported right now. "
    "What inventory change do you want to make (item + quantity) and for which outlet?"
)

def _first_name(full_name: str | None) -> str | None:
    if not isinstance(full_name, str):
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def process_session(*, session_id: str, task_id: str | None = None) -> None:
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None:
            logger.info(
                "process_session_noop_session_not_found task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return
        if db_session.status == "closed":
            logger.info(
                "process_session_noop_already_closed task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return

        sent_event = db.execute(
            select(ProcessingEvents)
            .where(
                ProcessingEvents.session_id == session_uuid,
                ProcessingEvents.event == "assistant_reply_sent_v0",
            )
            .order_by(ProcessingEvents.at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if sent_event is not None:
            logger.info(
                "process_session_noop_reply_already_sent task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            if db_session.status != "closed":
                now = dt.datetime.now(dt.UTC)
                db_session.status = "closed"
                if db_session.closed_at is None:
                    db_session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=now,
                        event="session_processed_v0",
                        payload_json=None,
                        error=None,
                    )
                )
                db.commit()
            return

        rows = list(
            db.execute(
                select(TelegramMessages, User.full_name)
                .join(User, TelegramMessages.user_id == User.id)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(
                    TelegramMessages.received_at.asc(), TelegramMessages.message_id.asc()
                )
            )
        )
        messages = [row[0] for row in rows]
        user_first_name = next(
            (_first_name(row[1]) for row in rows if isinstance(row[1], str) and row[1].strip()),
            None,
        )

        hint = db_session.hint_command
        file_kinds = [m.file_kind for m in messages if m.file_kind]
        mime_types = [m.mime for m in messages if m.mime]

        logger.info(
            "process_session_loaded task_id=%s session_id=%s status=%s message_count=%s hint=%s",
            task_id,
            session_id,
            db_session.status,
            len(messages),
            hint,
        )

        routing_plan = {
            "hint_command": hint,
            "message_count": len(messages),
            "file_kinds": file_kinds,
            "mime_types": mime_types,
        }

        settings = get_settings()
        on_topic, gate_reason, gate_data, gate_headers, gate_latency_ms = classify_on_topic(
            messages=messages,
            settings=settings,
            hint_command=hint,
        )

        if not on_topic:
            chat_state = get_or_create_chat_state(db=db, chat_id=db_session.chat_id)
            if chat_state.off_topic_mode:
                logger.info(
                    "process_session_off_topic_ghost task_id=%s session_id=%s chat_id=%s reason=%s",
                    task_id,
                    session_id,
                    db_session.chat_id,
                    gate_reason,
                )
                now = dt.datetime.now(dt.UTC)
                db_session.status = "closed"
                if db_session.closed_at is None:
                    db_session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=now,
                        event="session_off_topic_ghost_v0",
                        payload_json=json.dumps({"reason": gate_reason}, ensure_ascii=False)
                        if gate_reason
                        else None,
                        error=None,
                    )
                )
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=now,
                        event="session_processed_v0",
                        payload_json=None,
                        error=None,
                    )
                )
                db.commit()
                return

            logger.info(
                "process_session_off_topic_redirect task_id=%s session_id=%s chat_id=%s reason=%s",
                task_id,
                session_id,
                db_session.chat_id,
                gate_reason,
            )

            llm_call_id = None
            if gate_data:
                usage = extract_openrouter_usage(gate_data)
                generation_id = extract_openrouter_generation_id(
                    headers=gate_headers, data=gate_data
                )

                model_raw = gate_data.get("model")
                model = model_raw if isinstance(model_raw, str) else settings.openai_model

                upstream_id_raw = gate_data.get("id")
                upstream_id = (
                    upstream_id_raw if isinstance(upstream_id_raw, str) else None
                )

                provider_name_raw = gate_data.get("provider")
                provider_name = (
                    provider_name_raw if isinstance(provider_name_raw, str) else None
                )

                total_cost_usd = None
                usage_obj = gate_data.get("usage")
                if isinstance(usage_obj, dict) and isinstance(
                    usage_obj.get("cost"), (int, float)
                ):
                    total_cost_usd = float(usage_obj["cost"])

                llm_call_id = record_llm_call(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    purpose="gate",
                    model=model,
                    openrouter_generation_id=generation_id,
                    upstream_id=upstream_id,
                    provider_name=provider_name,
                    usage=usage,
                    latency_ms=gate_latency_ms,
                    total_cost_usd=total_cost_usd,
                    error=None,
                )

            telegram_message_id = send_message(
                chat_id=db_session.chat_id, text=OFF_TOPIC_REDIRECT_TEXT, settings=settings
            )
            if llm_call_id is not None:
                record_outgoing_message(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    kind="redirect",
                    text=OFF_TOPIC_REDIRECT_TEXT,
                    telegram_message_id=telegram_message_id,
                    llm_call_id=llm_call_id,
                )
            else:
                record_outgoing_message(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    kind="redirect",
                    text=OFF_TOPIC_REDIRECT_TEXT,
                    telegram_message_id=telegram_message_id,
                    llm_call_id=None,
                )

            set_chat_off_topic(db=db, chat_id=db_session.chat_id)

            now = dt.datetime.now(dt.UTC)
            db_session.status = "closed"
            if db_session.closed_at is None:
                db_session.closed_at = now
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=now,
                    event="session_off_topic_redirect_sent_v0",
                    payload_json=json.dumps({"reason": gate_reason}, ensure_ascii=False)
                    if gate_reason
                    else None,
                    error=None,
                )
            )
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=now,
                    event="session_processed_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db.commit()

            if llm_call_id is not None:
                try:
                    schedule_openrouter_cost_backfill(llm_call_id=llm_call_id, delay_seconds=120)
                except Exception:
                    logger.exception(
                        "process_session_cost_backfill_schedule_failed",
                        extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
                    )
            return

        chat_state = get_or_create_chat_state(db=db, chat_id=db_session.chat_id)

        capability_ok, capability_reject_text = classify_capability(
            messages=messages, hint_command=hint, settings=settings
        )
        if not capability_ok:
            if chat_state.off_topic_mode:
                logger.info(
                    "process_session_capability_ghost task_id=%s session_id=%s chat_id=%s",
                    task_id,
                    session_id,
                    db_session.chat_id,
                )
                now = dt.datetime.now(dt.UTC)
                db_session.status = "closed"
                if db_session.closed_at is None:
                    db_session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=now,
                        event="session_capability_ghost_v0",
                        payload_json=None,
                        error=None,
                    )
                )
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=now,
                        event="session_processed_v0",
                        payload_json=None,
                        error=None,
                    )
                )
                db.commit()
                return

            logger.info(
                "process_session_capability_reject task_id=%s session_id=%s chat_id=%s",
                task_id,
                session_id,
                db_session.chat_id,
            )
            telegram_message_id = send_message(
                chat_id=db_session.chat_id, text=capability_reject_text, settings=settings
            )
            record_outgoing_message(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                kind="capability_reject",
                text=capability_reject_text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )
            set_chat_off_topic(db=db, chat_id=db_session.chat_id)
            now = dt.datetime.now(dt.UTC)
            db_session.status = "closed"
            if db_session.closed_at is None:
                db_session.closed_at = now
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=now,
                    event="session_capability_reject_sent_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=now,
                    event="session_processed_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db.commit()
            return

        if is_outlet_list_request(messages=messages):
            reply_text = inventory_outlet_list_refusal()
            telegram_message_id = send_message(
                chat_id=db_session.chat_id, text=reply_text, settings=settings
            )
            record_outgoing_message(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                kind="reply",
                text=reply_text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )
            final_now = dt.datetime.now(dt.UTC)
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=final_now,
                    event="assistant_reply_sent_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db_session.status = "closed"
            if db_session.closed_at is None:
                db_session.closed_at = final_now
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=final_now,
                    event="session_processed_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db.commit()
            return

        if chat_state.off_topic_mode:
            set_chat_on_topic(db=db, chat_id=db_session.chat_id)

        now = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=now,
                event="router_plan_v0",
                payload_json=json.dumps(routing_plan),
                error=None,
            )
        )
        db.commit()

        generated_event = db.execute(
            select(ProcessingEvents)
            .where(
                ProcessingEvents.session_id == session_uuid,
                ProcessingEvents.event == "assistant_reply_generated_v0",
            )
            .order_by(ProcessingEvents.at.desc())
            .limit(1)
        ).scalar_one_or_none()

        reply_text: str | None = None
        if generated_event is not None and isinstance(generated_event.payload_json, str):
            try:
                payload = json.loads(generated_event.payload_json)
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    reply_text = payload["text"]
            except json.JSONDecodeError:
                reply_text = None

        reply_llm_call_id: uuid.UUID | None = None
        if reply_text is None:
            try:
                memory_summary = load_chat_memory_summary(db=db, chat_id=db_session.chat_id)
                history_messages = load_chat_history_messages(
                    db=db,
                    chat_id=db_session.chat_id,
                    exclude_session_id=session_uuid,
                    limit=50,
                )
                reply, metrics = generate_session_reply_with_metrics(
                    messages=messages,
                    hint_command=hint,
                    settings=settings,
                    memory_summary=memory_summary,
                    history_messages=history_messages,
                    user_first_name=user_first_name,
                )
            except OpenAIError as exc:
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=dt.datetime.now(dt.UTC),
                        event="assistant_reply_attempt_failed_v0",
                        payload_json=None,
                        error=str(exc),
                    )
                )
                db.commit()
                raise

            reply_text = reply.text
            reply_model = reply.model if isinstance(reply.model, str) else settings.openai_model
            generation_ids = metrics.get("openrouter_generation_ids") or []
            generation_id = (
                generation_ids[0]
                if isinstance(generation_ids, list) and len(generation_ids) == 1
                else None
            )
            usage = {
                "prompt_tokens": int(metrics.get("prompt_tokens_total") or 0),
                "completion_tokens": int(metrics.get("completion_tokens_total") or 0),
                "total_tokens": int(metrics.get("total_tokens_total") or 0),
            }
            total_cost_usd = metrics.get("cost_usd_total")
            total_cost_usd = float(total_cost_usd) if isinstance(total_cost_usd, (int, float)) else None
            latency_ms_total = metrics.get("latency_ms_total")
            latency_ms_total = int(latency_ms_total) if isinstance(latency_ms_total, int) else None

            llm_call_id = record_llm_call(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                purpose="reply",
                model=reply_model,
                openrouter_generation_id=generation_id,
                upstream_id=None,
                provider_name=None,
                usage=usage,
                latency_ms=latency_ms_total,
                total_cost_usd=total_cost_usd,
                error=None,
            )
            reply_llm_call_id = llm_call_id
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_generated_v0",
                    payload_json=json.dumps(
                        {"text": reply.text, "model": reply.model}, ensure_ascii=False
                    ),
                    error=None,
                )
            )
            db.commit()

            if generation_id is not None:
                try:
                    schedule_openrouter_cost_backfill(
                        llm_call_id=llm_call_id, delay_seconds=120
                    )
                except Exception:
                    logger.exception(
                        "process_session_cost_backfill_schedule_failed",
                        extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
                    )

        try:
            telegram_message_id = send_message(
                chat_id=db_session.chat_id, text=reply_text, settings=settings
            )
        except Exception as exc:
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_send_failed_v0",
                    payload_json=None,
                    error=repr(exc),
                )
            )
            db.commit()
            raise

        record_outgoing_message(
            db=db,
            session_id=session_uuid,
            chat_id=db_session.chat_id,
            kind="reply",
            text=reply_text,
            telegram_message_id=telegram_message_id,
            llm_call_id=reply_llm_call_id,
        )

        try:
            memory_summary = load_chat_memory_summary(db=db, chat_id=db_session.chat_id)
            history_messages = load_chat_history_messages(
                db=db,
                chat_id=db_session.chat_id,
                exclude_session_id=None,
                limit=50,
            )
            new_summary, sum_data, sum_headers, sum_latency_ms = update_chat_memory_summary(
                settings=settings,
                previous_summary=memory_summary,
                history_messages=history_messages,
            )
            if isinstance(new_summary, str) and new_summary.strip():
                row = db.scalar(
                    select(TelegramChatMemory).where(
                        TelegramChatMemory.chat_id == db_session.chat_id
                    )
                )
                if row is None:
                    row = TelegramChatMemory(chat_id=db_session.chat_id, summary_text=new_summary)
                    db.add(row)
                else:
                    row.summary_text = new_summary

                usage = extract_openrouter_usage(sum_data)
                generation_id = extract_openrouter_generation_id(
                    headers=sum_headers, data=sum_data
                )
                model_raw = sum_data.get("model")
                model = model_raw if isinstance(model_raw, str) else settings.openai_model
                usage_obj = sum_data.get("usage")
                total_cost_usd = (
                    float(usage_obj.get("cost"))
                    if isinstance(usage_obj, dict)
                    and isinstance(usage_obj.get("cost"), (int, float))
                    else None
                )
                llm_call_id = record_llm_call(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    purpose="memory",
                    model=model,
                    openrouter_generation_id=generation_id,
                    upstream_id=None,
                    provider_name=None,
                    usage=usage,
                    latency_ms=sum_latency_ms,
                    total_cost_usd=total_cost_usd,
                    error=None,
                )
                if generation_id is not None:
                    schedule_openrouter_cost_backfill(
                        llm_call_id=llm_call_id, delay_seconds=120
                    )
        except Exception:
            logger.exception(
                "chat_memory_update_failed",
                extra={"session_id": session_id, "chat_id": db_session.chat_id},
            )

        final_now = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=final_now,
                event="assistant_reply_sent_v0",
                payload_json=None,
                error=None,
            )
        )

        db_session.status = "closed"
        if db_session.closed_at is None:
            db_session.closed_at = final_now
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=final_now,
                event="session_processed_v0",
                payload_json=None,
                error=None,
            )
        )
        db.commit()
