from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.ai.chat_memory import (
    load_chat_history_messages,
    load_chat_memory_summary,
    update_chat_memory_summary,
)
from app.ai.db_router import classify_db_intent, extract_db_action
from app.ai.model_config import get_gate_model
from app.ai.openai_client import (
    OpenAIError,
    create_chat_completion_text_allow_empty_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.capability_gate import (
    classify_capability,
    inventory_outlet_list_refusal,
    is_db_engine_enabled,
    is_outlet_list_request,
)
from app.ai.session_reply import generate_session_reply_with_metrics
from app.ai.topic_gate import classify_on_topic
from app.core.config import Settings, get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_chat_memory import TelegramChatMemory
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.db_engine.read_executor import execute_read_action
from app.db_engine.write_executor import stage_cud_action
from app.domain.services.db_pending_action_service import DBPendingActionService
from app.domain.services.restaurant_service import RestaurantService
from app.policies.db_policy import normalize_db_action, validate_db_action
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
    "Only inventory updates are supported right now. "
    "What inventory change do you want to make (item + quantity) and for which outlet?"
)

PENDING_ACTION_PROMPT = (
    "You have a pending action. Please reply /confirm to proceed or /cancel to abort."
)


def _first_name(full_name: str | None) -> str | None:
    if not isinstance(full_name, str):
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


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


def _handle_db_error_and_close_session(
    *,
    db: Session,
    session_uuid: uuid.UUID,
    db_session: TelegramSessions,
    chat_id: int,
    error_msg: str,
    event_name: str,
    event_payload: dict[str, Any] | None = None,
    settings: Settings,
) -> None:
    """
    Handle DB action error: send message, record event, close session.

    Args:
        db: Database session
        session_uuid: Session UUID
        db_session: Telegram session object
        chat_id: Chat ID for sending message
        error_msg: Error message to send to user
        event_name: Processing event name
        event_payload: Optional payload for the event
        settings: Application settings
    """
    telegram_message_id = send_message(
        chat_id=chat_id, text=error_msg, settings=settings
    )
    record_outgoing_message(
        db=db,
        session_id=session_uuid,
        chat_id=chat_id,
        kind="reply",
        text=error_msg,
        telegram_message_id=telegram_message_id,
        llm_call_id=None,
    )
    db.add(
        ProcessingEvents(
            session_id=session_uuid,
            at=dt.datetime.now(dt.UTC),
            event=event_name,
            payload_json=json.dumps(event_payload, ensure_ascii=False)
            if event_payload
            else None,
            error=None,
        )
    )
    final_now = dt.datetime.now(dt.UTC)
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


def _generate_confirmation_message(
    *, action, settings, user_first_name: str | None = None
) -> tuple[str | None, dict[str, Any], dict[str, str], int]:
    """
    Generate a natural language confirmation message for a DB action.

    Uses a cheap model for cost efficiency.
    Returns (text_or_none, response_json, response_headers, latency_ms).
    """
    intent = action.intent if hasattr(action, "intent") else "perform this action"
    crud = action.crud if hasattr(action, "crud") else "change"
    table = action.table if hasattr(action, "table") else "record"
    values = action.values if hasattr(action, "values") else {}

    # Fix: convert dict_items to list before slicing
    values_items = list((values or {}).items())[:3]
    values_summary = ", ".join(f"{k}={v}" for k, v in values_items)
    if values_summary:
        action_desc = f"{crud} {table} ({values_summary})"
    else:
        action_desc = f"{crud} {table}"

    system_prompt = (
        "You are generating a confirmation message for a database action.\n"
        "Write a clear, concise message that:\n"
        "- Explains what will happen\n"
        "- Uses natural language\n"
        "- Ends with 'Reply /confirm to proceed or /cancel to abort.'\n"
        "- Is 1-2 sentences maximum\n"
        "Output ONLY the confirmation message text, nothing else."
    )

    user_prompt = (
        f"Action: {intent}\nOperation: {action_desc}\nGenerate confirmation message:"
    )

    if isinstance(user_first_name, str) and user_first_name.strip():
        user_prompt = f"User: {user_first_name.strip()}\n{user_prompt}"

    # Use cheap model for confirmation message generation
    gate_model = get_gate_model(settings)
    cheap_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    text, data, headers, latency_ms = (
        create_chat_completion_text_allow_empty_with_http_info(
            settings=cheap_settings,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
    )

    if text is None or not text.strip():
        fallback = f"I'll {intent}. Reply /confirm to proceed or /cancel to abort."
        return fallback, data, headers, latency_ms

    cleaned = text.strip()
    if not cleaned.endswith("."):
        cleaned += "."
    if "/confirm" not in cleaned.lower() and "/cancel" not in cleaned.lower():
        cleaned += " Reply /confirm to proceed or /cancel to abort."

    return cleaned, data, headers, latency_ms


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
        on_topic, gate_reason, gate_data, gate_headers, gate_latency_ms = (
            classify_on_topic(
                messages=messages,
                settings=settings,
                hint_command=hint,
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
            # Cost backfill already scheduled above with gate_llm_call_id
            return

        chat_state = get_or_create_chat_state(db=db, chat_id=db_session.chat_id)

        # Get user from messages
        user = None
        if messages:
            user = db.scalar(select(User).where(User.id == messages[0].user_id))

        # DB Engine routing - runs BEFORE inventory capability gate
        # Only if db_engine capability is enabled
        db_engine_usable = (
            is_db_engine_enabled(settings=settings)
            and isinstance(settings.openai_api_key, str)
            and settings.openai_api_key.strip().lower() not in {"", "test"}
        )
        if user is not None and db_engine_usable:
            # Check for pending DB actions first - short circuit if exists
            pending_service = DBPendingActionService(db)
            pending_action = pending_service.get_latest_pending(user_id=user.id)
            if pending_action is not None:
                # User has a pending action - prompt them to confirm or cancel
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
                return

            # Get user role and restaurants before DB routing
            actor_role, restaurant_roles = _get_user_role_and_restaurants(
                db=db, user_id=user.id
            )

            # Load chat history for context-aware extraction
            history_messages = load_chat_history_messages(
                db=db,
                chat_id=db_session.chat_id,
                exclude_session_id=session_uuid,
                limit=50,
            )

            # DB Intent Classification
            intent_result = classify_db_intent(
                messages=messages, settings=settings, actor_role=actor_role
            )
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="db_intent_detected_v0",
                    payload_json=json.dumps(
                        {
                            "is_db_action": intent_result.is_db_action,
                            "reason": intent_result.reason,
                            "confidence": intent_result.confidence,
                        },
                        ensure_ascii=False,
                    ),
                    error=None,
                )
            )

            # Handle capability queries
            if intent_result.is_capability_query:
                from app.ai.db_schema import get_allowed_tables_for_role

                schema = get_allowed_tables_for_role(role=actor_role)

                # Create a concise summary for the user
                summary_lines = [f"You have '{actor_role}' role. You can access:\n"]
                for table_name, table_info in schema.items():
                    permissions = table_info.get("permissions", {})
                    if permissions:
                        crud_ops = list(permissions.keys())
                        summary_lines.append(f"- {table_name}: {', '.join(crud_ops)}")

                reply_text = "\n".join(summary_lines)
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

            if intent_result.is_db_action:
                # Record LLM call for intent classification
                usage = extract_openrouter_usage(intent_result.data)
                generation_id = extract_openrouter_generation_id(
                    headers=intent_result.headers, data=intent_result.data
                )
                model_raw = intent_result.data.get("model")
                model = (
                    model_raw if isinstance(model_raw, str) else settings.openai_model
                )
                upstream_id_raw = intent_result.data.get("id")
                upstream_id = (
                    upstream_id_raw if isinstance(upstream_id_raw, str) else None
                )
                provider_name_raw = intent_result.data.get("provider")
                provider_name = (
                    provider_name_raw if isinstance(provider_name_raw, str) else None
                )
                total_cost_usd = _extract_cost_from_usage(intent_result.data)

                record_llm_call(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    purpose="db_intent_classify",
                    model=model,
                    openrouter_generation_id=generation_id,
                    upstream_id=upstream_id,
                    provider_name=provider_name,
                    usage=usage,
                    latency_ms=intent_result.latency_ms,
                    total_cost_usd=total_cost_usd,
                    error=None,
                )

                # Extract DB action with history context
                action_result = extract_db_action(
                    messages=messages,
                    settings=settings,
                    actor_role=actor_role,
                    history_messages=history_messages,
                )

                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=dt.datetime.now(dt.UTC),
                        event="db_action_extracted_v0",
                        payload_json=json.dumps(
                            {
                                "action_id": action_result.action.action_id
                                if action_result.action
                                else None,
                                "crud": action_result.action.crud
                                if action_result.action
                                else None,
                                "table": action_result.action.table
                                if action_result.action
                                else None,
                                "errors": action_result.errors,
                            },
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )

                # Record LLM call for action extraction
                usage = extract_openrouter_usage(action_result.data)
                generation_id = extract_openrouter_generation_id(
                    headers=action_result.headers, data=action_result.data
                )
                model_raw = action_result.data.get("model")
                model = (
                    model_raw if isinstance(model_raw, str) else settings.openai_model
                )
                upstream_id_raw = action_result.data.get("id")
                upstream_id = (
                    upstream_id_raw if isinstance(upstream_id_raw, str) else None
                )
                provider_name_raw = action_result.data.get("provider")
                provider_name = (
                    provider_name_raw if isinstance(provider_name_raw, str) else None
                )
                total_cost_usd = _extract_cost_from_usage(action_result.data)

                record_llm_call(
                    db=db,
                    session_id=session_uuid,
                    chat_id=db_session.chat_id,
                    purpose="db_action_extract",
                    model=model,
                    openrouter_generation_id=generation_id,
                    upstream_id=upstream_id,
                    provider_name=provider_name,
                    usage=usage,
                    latency_ms=action_result.latency_ms,
                    total_cost_usd=total_cost_usd,
                    error=None,
                )

                if action_result.action is None:
                    # Extraction failed
                    error_msg = "I couldn't understand that database request. " + (
                        " ".join(action_result.errors[:2])
                        if action_result.errors
                        else "Please try rephrasing."
                    )
                    _handle_db_error_and_close_session(
                        db=db,
                        session_uuid=session_uuid,
                        db_session=db_session,
                        chat_id=db_session.chat_id,
                        error_msg=error_msg,
                        event_name="db_action_validation_failed_v0",
                        event_payload={"errors": action_result.errors},
                        settings=settings,
                    )
                    return

                # Normalize and validate action (with per-restaurant role checking)
                normalized_result = normalize_db_action(
                    action=action_result.action,
                    actor_user_id=str(user.id),
                    actor_role=actor_role,
                    restaurant_roles=restaurant_roles,
                )

                if normalized_result.normalized is None:
                    error_msg = "I can't perform that action. " + (
                        " ".join(normalized_result.reasons[:2])
                        if normalized_result.reasons
                        else "Please check your permissions."
                    )
                    _handle_db_error_and_close_session(
                        db=db,
                        session_uuid=session_uuid,
                        db_session=db_session,
                        chat_id=db_session.chat_id,
                        error_msg=error_msg,
                        event_name="db_action_validation_failed_v0",
                        event_payload={"reasons": normalized_result.reasons},
                        settings=settings,
                    )
                    return

                normalized_action = normalized_result.normalized
                validation_result = validate_db_action(
                    action=normalized_action,
                    actor_user_id=str(user.id),
                    actor_role=actor_role,
                    restaurant_roles=restaurant_roles,
                )

                if not validation_result.allowed:
                    error_msg = "I can't perform that action. " + (
                        " ".join(validation_result.reasons[:2])
                        if validation_result.reasons
                        else "Please check your permissions."
                    )
                    _handle_db_error_and_close_session(
                        db=db,
                        session_uuid=session_uuid,
                        db_session=db_session,
                        chat_id=db_session.chat_id,
                        error_msg=error_msg,
                        event_name="db_action_validation_failed_v0",
                        event_payload={"reasons": validation_result.reasons},
                        settings=settings,
                    )
                    return

                # Execute action based on CRUD type
                if normalized_action.crud == "read":
                    # Execute read immediately
                    try:
                        results = execute_read_action(
                            session=db, action=normalized_action
                        )
                        # Format results as text
                        if not results:
                            reply_text = "No records found."
                        else:
                            result_lines = []
                            for i, result in enumerate(
                                results[:10], 1
                            ):  # Limit to 10 results
                                parts = [f"{k}: {v}" for k, v in result.items()]
                                result_lines.append(f"{i}. " + ", ".join(parts))
                            reply_text = "\n".join(result_lines)
                            if len(results) > 10:
                                reply_text += f"\n... and {len(results) - 10} more."

                        telegram_message_id = send_message(
                            chat_id=db_session.chat_id,
                            text=reply_text,
                            settings=settings,
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
                        db.add(
                            ProcessingEvents(
                                session_id=session_uuid,
                                at=dt.datetime.now(dt.UTC),
                                event="db_read_executed_v0",
                                payload_json=json.dumps(
                                    {"result_count": len(results)}, ensure_ascii=False
                                ),
                                error=None,
                            )
                        )
                    except Exception as exc:
                        logger.exception(
                            "db_read_execution_failed",
                            extra={
                                "session_id": session_id,
                                "action_id": normalized_action.action_id,
                            },
                        )
                        error_msg = "Failed to read data. Please try again."
                        _handle_db_error_and_close_session(
                            db=db,
                            session_uuid=session_uuid,
                            db_session=db_session,
                            chat_id=db_session.chat_id,
                            error_msg=error_msg,
                            event_name="db_read_execution_failed_v0",
                            event_payload={"error": str(exc)},
                            settings=settings,
                        )
                        return

                elif normalized_action.crud in {"create", "update", "delete"}:
                    # Check for duplicates before staging (for create operations)
                    if normalized_action.crud == "create":
                        from app.db_engine.write_executor import _check_duplicate

                        duplicate_info = _check_duplicate(
                            session=db,
                            action=normalized_action,
                            values=normalized_action.values or {},
                        )
                        if duplicate_info:
                            error_msg = f"Cannot create: {duplicate_info}"
                            _handle_db_error_and_close_session(
                                db=db,
                                session_uuid=session_uuid,
                                db_session=db_session,
                                chat_id=db_session.chat_id,
                                error_msg=error_msg,
                                event_name="db_duplicate_detected_v0",
                                event_payload={"duplicate_info": duplicate_info},
                                settings=settings,
                            )
                            return

                    # Stage CUD action for confirmation
                    try:
                        pending_row = stage_cud_action(
                            session=db,
                            user_id=user.id,
                            action=normalized_action,
                            chat_id=db_session.chat_id,
                            session_id=session_uuid,
                        )

                        # Generate confirmation message
                        conf_text, conf_data, conf_headers, conf_latency_ms = (
                            _generate_confirmation_message(
                                action=normalized_action,
                                settings=settings,
                                user_first_name=user_first_name,
                            )
                        )

                        # Record LLM call for confirmation message
                        conf_usage = extract_openrouter_usage(conf_data)
                        conf_generation_id = extract_openrouter_generation_id(
                            headers=conf_headers, data=conf_data
                        )
                        conf_model_raw = conf_data.get("model")
                        conf_model = (
                            conf_model_raw
                            if isinstance(conf_model_raw, str)
                            else settings.openai_model
                        )
                        conf_upstream_id_raw = conf_data.get("id")
                        conf_upstream_id = (
                            conf_upstream_id_raw
                            if isinstance(conf_upstream_id_raw, str)
                            else None
                        )
                        conf_provider_name_raw = conf_data.get("provider")
                        conf_provider_name = (
                            conf_provider_name_raw
                            if isinstance(conf_provider_name_raw, str)
                            else None
                        )
                        conf_total_cost_usd = _extract_cost_from_usage(conf_data)

                        conf_llm_call_id = record_llm_call(
                            db=db,
                            session_id=session_uuid,
                            chat_id=db_session.chat_id,
                            purpose="db_confirmation_message",
                            model=conf_model,
                            openrouter_generation_id=conf_generation_id,
                            upstream_id=conf_upstream_id,
                            provider_name=conf_provider_name,
                            usage=conf_usage,
                            latency_ms=conf_latency_ms,
                            total_cost_usd=conf_total_cost_usd,
                            error=None,
                        )

                        if conf_generation_id is not None:
                            try:
                                schedule_openrouter_cost_backfill(
                                    llm_call_id=conf_llm_call_id, delay_seconds=120
                                )
                            except Exception:
                                logger.exception(
                                    "db_confirmation_cost_backfill_schedule_failed",
                                    extra={
                                        "llm_call_id": str(conf_llm_call_id),
                                        "session_id": session_id,
                                    },
                                )

                        conf_text_safe = conf_text or "Please confirm to proceed."
                        telegram_message_id = send_message(
                            chat_id=db_session.chat_id,
                            text=conf_text_safe,
                            settings=settings,
                        )
                        record_outgoing_message(
                            db=db,
                            session_id=session_uuid,
                            chat_id=db_session.chat_id,
                            kind="reply",
                            text=conf_text_safe,
                            telegram_message_id=telegram_message_id,
                            llm_call_id=conf_llm_call_id,
                        )
                        db.add(
                            ProcessingEvents(
                                session_id=session_uuid,
                                at=dt.datetime.now(dt.UTC),
                                event="db_action_staged_v0",
                                payload_json=json.dumps(
                                    {"pending_action_id": str(pending_row.id)},
                                    ensure_ascii=False,
                                ),
                                error=None,
                            )
                        )
                    except Exception as exc:
                        logger.exception(
                            "db_action_staging_failed",
                            extra={
                                "session_id": session_id,
                                "action_id": normalized_action.action_id,
                            },
                        )
                        error_msg = "Failed to prepare that action. Please try again."
                        _handle_db_error_and_close_session(
                            db=db,
                            session_uuid=session_uuid,
                            db_session=db_session,
                            chat_id=db_session.chat_id,
                            error_msg=error_msg,
                            event_name="db_action_staging_failed_v0",
                            event_payload={"error": str(exc)},
                            settings=settings,
                        )
                        return

        # Inventory capability gate (runs AFTER db_engine routing)
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
                chat_id=db_session.chat_id,
                text=capability_reject_text,
                settings=settings,
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
        if generated_event is not None and isinstance(
            generated_event.payload_json, str
        ):
            try:
                payload = json.loads(generated_event.payload_json)
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    reply_text = payload["text"]
            except json.JSONDecodeError:
                reply_text = None

        reply_llm_call_id: uuid.UUID | None = None
        if reply_text is None:
            try:
                memory_summary = load_chat_memory_summary(
                    db=db, chat_id=db_session.chat_id
                )
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
            reply_model = (
                reply.model if isinstance(reply.model, str) else settings.openai_model
            )
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
            total_cost_usd = _safe_float(metrics.get("cost_usd_total"))
            latency_ms_total = metrics.get("latency_ms_total")
            latency_ms_total = (
                int(latency_ms_total) if isinstance(latency_ms_total, int) else None
            )

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
                        extra={
                            "llm_call_id": str(llm_call_id),
                            "session_id": session_id,
                        },
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

        reply_text_safe = reply_text or ""
        record_outgoing_message(
            db=db,
            session_id=session_uuid,
            chat_id=db_session.chat_id,
            kind="reply",
            text=reply_text_safe,
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
            new_summary, sum_data, sum_headers, sum_latency_ms = (
                update_chat_memory_summary(
                    settings=settings,
                    previous_summary=memory_summary,
                    history_messages=history_messages,
                )
            )
            if isinstance(new_summary, str) and new_summary.strip():
                row = db.scalar(
                    select(TelegramChatMemory).where(
                        TelegramChatMemory.chat_id == db_session.chat_id
                    )
                )
                if row is None:
                    row = TelegramChatMemory(
                        chat_id=db_session.chat_id, summary_text=new_summary
                    )
                    db.add(row)
                else:
                    row.summary_text = new_summary

                usage = extract_openrouter_usage(sum_data)
                generation_id = extract_openrouter_generation_id(
                    headers=sum_headers, data=sum_data
                )
                model_raw = sum_data.get("model")
                model = (
                    model_raw if isinstance(model_raw, str) else settings.openai_model
                )
                total_cost_usd = _extract_cost_from_usage(sum_data)
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
