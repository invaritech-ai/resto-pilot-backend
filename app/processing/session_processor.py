from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

from sqlalchemy import select

from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.session_reply import generate_session_reply
from app.ai.topic_gate import classify_on_topic
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
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
    "I can help with restaurant/outlet operations (invoices, inventory, menu, pricing, staff). "
    "What are you working on right now?"
)


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

        messages = list(
            db.scalars(
                select(TelegramMessages)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(
                    TelegramMessages.received_at.asc(), TelegramMessages.message_id.asc()
                )
            )
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
                model = (
                    gate_data.get("model")
                    if isinstance(gate_data.get("model"), str)
                    else settings.openai_model
                )
                upstream_id = (
                    gate_data.get("id") if isinstance(gate_data.get("id"), str) else None
                )
                provider_name = (
                    gate_data.get("provider")
                    if isinstance(gate_data.get("provider"), str)
                    else None
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

        if reply_text is None:
            try:
                reply = generate_session_reply(
                    messages=messages, hint_command=hint, settings=settings
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

        try:
            send_message(chat_id=db_session.chat_id, text=reply_text, settings=settings)
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
