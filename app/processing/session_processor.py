from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.ai.agent import run_agent_loop
from app.ai.chat_memory import (
    load_chat_history_messages,
    load_chat_memory_summary,
    update_chat_memory_summary,
)
from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.topic_gate import classify_on_topic
from app.core.config import Settings, get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_chat_memory import TelegramChatMemory
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.domain.services.db_pending_action_service import DBPendingActionService
from app.domain.services.restaurant_service import RestaurantService
from app.policies.db_allowlist import ROLE_OWNER, ROLE_STAFF
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
    "I can help with restaurant operations. What would you like to know or do?"
)

PENDING_ACTION_PROMPT = (
    "You have a pending action. Please reply /confirm to proceed or /cancel to abort."
)


def _first_name(full_name: str | None) -> str | None:
    if not isinstance(full_name, str):
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


def _update_chat_memory(
    *,
    db: Session,
    chat_id: int,
    session_uuid: uuid.UUID,
    settings: Settings,
) -> None:
    """
    Update chat memory with the latest conversation history.
    Should be called after every meaningful interaction, not just LLM replies.

    This ensures the rolling summary stays up to date even for:
    - Static responses (off-topic redirects, pending action prompts)
    - DB engine actions (reads, staged CUD operations)
    - Capability rejections
    """
    try:
        previous_summary = load_chat_memory_summary(db=db, chat_id=chat_id)
        history = load_chat_history_messages(
            db=db,
            chat_id=chat_id,
            exclude_session_id=None,  # Include current session
            limit=50,
        )

        new_summary, sum_data, sum_headers, sum_latency_ms = update_chat_memory_summary(
            settings=settings,
            previous_summary=previous_summary,
            history_messages=history,
        )

        if isinstance(new_summary, str) and new_summary.strip():
            row = db.scalar(
                select(TelegramChatMemory).where(TelegramChatMemory.chat_id == chat_id)
            )
            if row is None:
                row = TelegramChatMemory(chat_id=chat_id, summary_text=new_summary)
                db.add(row)
            else:
                row.summary_text = new_summary

            usage = extract_openrouter_usage(sum_data)
            generation_id = extract_openrouter_generation_id(
                headers=sum_headers, data=sum_data
            )
            model_raw = sum_data.get("model")
            model = model_raw if isinstance(model_raw, str) else settings.openai_model
            total_cost_usd = _extract_cost_from_usage(sum_data)

            llm_call_id = record_llm_call(
                db=db,
                session_id=session_uuid,
                chat_id=chat_id,
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
                try:
                    schedule_openrouter_cost_backfill(
                        llm_call_id=llm_call_id, delay_seconds=120
                    )
                except Exception:
                    logger.exception(
                        "memory_cost_backfill_schedule_failed",
                        extra={"llm_call_id": str(llm_call_id), "chat_id": chat_id},
                    )
            db.commit()
    except Exception:
        logger.exception(
            "chat_memory_update_failed",
            extra={"session_uuid": str(session_uuid), "chat_id": chat_id},
        )


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _safe_float(value: Any) -> float | None:
    """Return float(value) if numeric, else None."""
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_cost_from_usage(data: dict[str, Any]) -> float | None:
    """
    Extract cost from LLM response data.

    Args:
        data: Response data dictionary containing usage information

    Returns:
        Cost as float, or None if not available
    """
    usage_obj = data.get("usage")
    if not isinstance(usage_obj, dict):
        return None
    return _safe_float(usage_obj.get("cost"))


def _get_user_role_and_restaurants(
    *, db: Session, user_id: uuid.UUID
) -> tuple[str, dict[str, str]]:
    """
    Get user's role and restaurant IDs.

    Returns:
        (highest_role, restaurant_roles) where:
        - highest_role is "owner" or "staff" (highest role if multiple restaurants)
        - restaurant_roles is a map of restaurant_id -> role for per-restaurant checks
    """
    rows = RestaurantService(db).list_for_user(user_id=user_id)
    if not rows:
        return ROLE_STAFF, {}

    restaurant_roles: dict[str, str] = {}
    has_owner = False
    for _restaurant, membership in rows:
        rid = str(membership.restaurant_id)
        restaurant_roles[rid] = membership.role
        if membership.role == ROLE_OWNER:
            has_owner = True

    highest_role = ROLE_OWNER if has_owner else ROLE_STAFF
    return highest_role, restaurant_roles


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
                    TelegramMessages.received_at.asc(),
                    TelegramMessages.message_id.asc(),
                )
            )
        )
        messages = [row[0] for row in rows]
        user_first_name = next(
            (
                _first_name(row[1])
                for row in rows
                if isinstance(row[1], str) and row[1].strip()
            ),
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

        # CONTEXT FIRST: Load history and memory BEFORE any classification
        history_messages = load_chat_history_messages(
            db=db,
            chat_id=db_session.chat_id,
            exclude_session_id=session_uuid,
            limit=50,
        )
        memory_summary = load_chat_memory_summary(db=db, chat_id=db_session.chat_id)

        logger.info(
            "process_session_context_loaded task_id=%s session_id=%s history_count=%s has_memory=%s",
            task_id,
            session_id,
            len(history_messages),
            memory_summary is not None,
        )

        on_topic, gate_reason, gate_data, gate_headers, gate_latency_ms = (
            classify_on_topic(
                messages=messages,
                settings=settings,
                hint_command=hint,
                history_messages=history_messages,
                memory_summary=memory_summary,
            )
        )

        # Always log the topic gate LLM call for telemetry
        gate_llm_call_id = None
        if gate_data:
            usage = extract_openrouter_usage(gate_data)
            generation_id = extract_openrouter_generation_id(
                headers=gate_headers, data=gate_data
            )
            model_raw = gate_data.get("model")
            model = model_raw if isinstance(model_raw, str) else settings.openai_model
            upstream_id_raw = gate_data.get("id")
            upstream_id = upstream_id_raw if isinstance(upstream_id_raw, str) else None
            provider_name_raw = gate_data.get("provider")
            provider_name = (
                provider_name_raw if isinstance(provider_name_raw, str) else None
            )
            total_cost_usd = _extract_cost_from_usage(gate_data)

            gate_llm_call_id = record_llm_call(
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
            db.commit()

            if generation_id is not None:
                try:
                    schedule_openrouter_cost_backfill(
                        llm_call_id=gate_llm_call_id, delay_seconds=120
                    )
                except Exception:
                    logger.exception(
                        "process_session_gate_cost_backfill_schedule_failed",
                        extra={
                            "llm_call_id": str(gate_llm_call_id),
                            "session_id": session_id,
                        },
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
                        payload_json=json.dumps(
                            {"reason": gate_reason}, ensure_ascii=False
                        )
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
                _update_chat_memory(
                    db=db,
                    chat_id=db_session.chat_id,
                    session_uuid=session_uuid,
                    settings=settings,
                )
                return

            logger.info(
                "process_session_off_topic_redirect task_id=%s session_id=%s chat_id=%s reason=%s",
                task_id,
                session_id,
                db_session.chat_id,
                gate_reason,
            )

            telegram_message_id = send_message(
                chat_id=db_session.chat_id,
                text=OFF_TOPIC_REDIRECT_TEXT,
                settings=settings,
            )
            record_outgoing_message(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                kind="redirect",
                text=OFF_TOPIC_REDIRECT_TEXT,
                telegram_message_id=telegram_message_id,
                llm_call_id=gate_llm_call_id,
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
            _update_chat_memory(
                db=db,
                chat_id=db_session.chat_id,
                session_uuid=session_uuid,
                settings=settings,
            )
            return

        chat_state = get_or_create_chat_state(db=db, chat_id=db_session.chat_id)

        # Get user from messages
        user = None
        if messages:
            user = db.scalar(select(User).where(User.id == messages[0].user_id))

        # Check for pending DB actions (deterministic - must be handled before agent loop)
        if user is not None:
            pending_service = DBPendingActionService(db)
            pending_action = pending_service.get_latest_pending(user_id=user.id)
            if pending_action is not None:
                logger.info(
                    "process_session_pending_action_exists task_id=%s session_id=%s user_id=%s pending_id=%s",
                    task_id,
                    session_id,
                    str(user.id),
                    str(pending_action.id),
                )
                telegram_message_id = send_message(
                    chat_id=db_session.chat_id,
                    text=PENDING_ACTION_PROMPT,
                    settings=settings,
                )
                record_outgoing_message(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    kind="reply",
                    text=PENDING_ACTION_PROMPT,
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
                _update_chat_memory(
                    db=db,
                    chat_id=db_session.chat_id,
                    session_uuid=session_uuid,
                    settings=settings,
                )
                return

        # Get user role and restaurants for agent tools
        actor_role, restaurant_roles = (
            _get_user_role_and_restaurants(db=db, user_id=user.id)
            if user
            else (ROLE_STAFF, {})
        )

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

        # Run the agent loop with tool-calling support
        # Agent loop records each LLM call individually for accurate cost tracking
        try:
            agent_result = run_agent_loop(
                messages=messages,
                db=db,
                user_id=user.id
                if user
                else uuid.UUID("00000000-0000-0000-0000-000000000000"),
                actor_role=actor_role,
                restaurant_roles=restaurant_roles,
                settings=settings,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                history_messages=history_messages,
                memory_summary=memory_summary,
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

        reply_text = agent_result.text
        reply_model = agent_result.model
        metrics = agent_result.metrics

        # LLM calls are already recorded per-call in agent loop
        # Use final_llm_call_id for outgoing message attribution (the call that generated the response)
        # This is None if the response is a fallback/error message not generated by an LLM
        reply_llm_call_id = agent_result.final_llm_call_id

        # Record the reply event with aggregated metrics for easy querying
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=dt.datetime.now(dt.UTC),
                event="assistant_reply_generated_v0",
                payload_json=json.dumps(
                    {
                        "text": reply_text,
                        "model": reply_model,
                        "tool_calls": agent_result.tool_calls_made,
                        "llm_call_ids": [str(cid) for cid in agent_result.llm_call_ids],
                        "call_count": metrics.get("call_count", 0),
                        "total_tokens": metrics.get("total_tokens_total", 0),
                        "cost_usd_total": metrics.get("cost_usd_total", 0.0),
                    },
                    ensure_ascii=False,
                ),
                error=None,
            )
        )
        db.commit()

        # Send reply
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

        # Update memory
        _update_chat_memory(
            db=db,
            chat_id=db_session.chat_id,
            session_uuid=session_uuid,
            settings=settings,
        )

        # Close session
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
