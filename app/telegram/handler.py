"""
Unified handler for Telegram updates.

This module consolidates the routing and business logic for processing
Telegram updates, keeping the webhook route thin.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.db_engine.write_executor import execute_confirmed_action
from app.policies.db_allowlist import ROLE_OWNER, ROLE_STAFF
from app.domain.services.restaurant_service import RestaurantService
from app.domain.services.db_pending_action_service import DBPendingActionService
from app.policies.db_policy import normalize_db_action, validate_db_action
from app.schemas.db_action import parse_db_action
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.bot_api import send_message
from app.telegram.commands import extract_command
from app.telegram.ingest import ingest_update, parse_update, seal_open_session
from app.telegram.processor import process_update
from app.workers.celery_types import CeleryDelayable
from app.workers.telemetry import record_outgoing_message

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Reserved commands that bypass batching and are processed immediately.
# This is the single source of truth - add new instant commands here.
# -----------------------------------------------------------------------------
INSTANT_COMMANDS: frozenset[str] = frozenset(
    {
        "/start",
        "/respond",
        "/done",
        "/confirm",
        "/cancel",
    }
)

FORCE_FLUSH_COMMANDS: frozenset[str] = frozenset(
    {
        "/respond",
        "/done",
    }
)

FORCE_FLUSH_REPLY_TEXT = "Got it — I'm on it."
NOTHING_TO_PROCESS_REPLY_TEXT = "Nothing to process right now."


def _send_and_log_message(
    *,
    db: Session,
    chat_id: int,
    text: str,
    kind: str,
    settings: Settings,
    session_id: uuid.UUID | None = None,
    llm_call_id: uuid.UUID | None = None,
) -> None:
    """
    Send a message and log it to telegram_outgoing_messages.
    Creates a closed session if session_id is not provided.
    """
    telegram_message_id = send_message(chat_id=chat_id, text=text, settings=settings)

    if session_id is None:
        # Create a closed session for logging
        now = dt.datetime.now(dt.UTC)
        closed_session = TelegramSessions(
            chat_id=chat_id,
            started_at=now,
            last_activity_at=now,
            flush_at=now,
            status="closed",
            hint_command=None,
            closed_at=now,
            ack_sent_at=None,
        )
        db.add(closed_session)
        db.flush()
        session_id = closed_session.id

    record_outgoing_message(
        db=db,
        session_id=session_id,
        chat_id=chat_id,
        kind=kind,
        text=text,
        telegram_message_id=telegram_message_id,
        llm_call_id=llm_call_id,
    )
    db.commit()


def _persist_update_to_closed_session(
    *, update: dict, db: Session, user: User
) -> TelegramSessions | None:
    parsed = parse_update(update)
    if parsed is None:
        return

    now = dt.datetime.now(dt.UTC)
    session_row = TelegramSessions(
        chat_id=parsed.chat_id,
        started_at=now,
        last_activity_at=now,
        flush_at=now,
        status="closed",
        hint_command=None,
        closed_at=now,
        ack_sent_at=None,
    )
    db.add(session_row)
    db.flush()

    db.add(
        TelegramMessages(
            session_id=session_row.id,
            chat_id=parsed.chat_id,
            user_id=user.id,
            telegram_id=parsed.telegram_id,
            message_id=parsed.message_id,
            update_id=parsed.update_id,
            received_at=parsed.received_at,
            text=parsed.text,
            caption=parsed.caption,
            file_id=parsed.file_id,
            file_unique_id=parsed.file_unique_id,
            file_kind=parsed.file_kind,
            mime=parsed.mime,
            filename=parsed.filename,
            size=parsed.size,
        )
    )
    db.add(
        ProcessingEvents(
            session_id=session_row.id,
            at=now,
            event="ingested_update",
            payload_json=None,
            error=None,
        )
    )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _persist_update_to_session(
    *, update: dict, db: Session, session_id: uuid.UUID
) -> None:
    parsed = parse_update(update)
    if parsed is None:
        return

    user = db.scalar(select(User).where(User.telegram_id == parsed.telegram_id))
    if user is None:
        return

    now = dt.datetime.now(dt.UTC)
    db.add(
        TelegramMessages(
            session_id=session_id,
            chat_id=parsed.chat_id,
            user_id=user.id,
            telegram_id=parsed.telegram_id,
            message_id=parsed.message_id,
            update_id=parsed.update_id,
            received_at=parsed.received_at,
            text=parsed.text,
            caption=parsed.caption,
            file_id=parsed.file_id,
            file_unique_id=parsed.file_unique_id,
            file_kind=parsed.file_kind,
            mime=parsed.mime,
            filename=parsed.filename,
            size=parsed.size,
        )
    )
    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=now,
            event="ingested_update",
            payload_json=None,
            error=None,
        )
    )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _extract_command_from_update(update: dict) -> tuple[str | None, str | None]:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None, None

    text = message.get("text") if isinstance(message.get("text"), str) else None
    caption = (
        message.get("caption") if isinstance(message.get("caption"), str) else None
    )
    return extract_command(text, caption)


def _is_instant_command(update: dict) -> bool:
    """Check if the update contains an instant command that bypasses batching."""
    command, _args = _extract_command_from_update(update)
    return command is not None and command in INSTANT_COMMANDS


def _ensure_user_exists_for_update(update: dict, db: Session) -> None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return

    chat_id = (message.get("chat") or {}).get("id")
    user_info = message.get("from") or {}
    telegram_id = user_info.get("id")
    if not isinstance(chat_id, int) or not isinstance(telegram_id, int):
        return

    payload = TelegramUserCreate(
        telegram_id=telegram_id,
        chat_id=chat_id,
        first_name=user_info.get("first_name"),
        last_name=user_info.get("last_name"),
        username=user_info.get("username"),
    )
    UserService(db).get_or_create(payload)


def handle_update(update: dict, db: Session, settings: Settings) -> None:
    """
    Process a Telegram update with the appropriate handler based on settings.

    This function encapsulates the routing logic that determines whether to
    use batching mode (ingest_update) or immediate processing (process_update).

    Reserved commands (/start, /respond, /done) always bypass batching and are
    processed immediately, even when telegram_batching_enabled=True.

    When batching is enabled, the FastAPI webhook route is the single source
    of truth for ingesting non-instant messages into sessions. This handler
    should only be invoked for instant routes (e.g., /start) and instant
    stateful flows (e.g., phone intake).

    The webhook endpoint should always return quickly; any responses to users
    are sent via the Telegram Bot API from background workers.

    Args:
        update: The Telegram update dictionary from the webhook
        db: Database session for persistence operations
        settings: Application settings including batching configuration
    """
    update_id = update.get("update_id") if isinstance(update, dict) else None
    message = (
        (update.get("message") or update.get("edited_message") or {})
        if isinstance(update, dict)
        else {}
    )
    chat_id = (
        (message.get("chat") or {}).get("id") if isinstance(message, dict) else None
    )

    logger.info(
        "handle_update_received update_id=%s chat_id=%s batching_enabled=%s",
        update_id,
        chat_id,
        settings.telegram_batching_enabled,
    )

    command, _args = _extract_command_from_update(update)
    logger.info(
        "handle_update_routing update_id=%s chat_id=%s command=%s",
        update_id,
        chat_id,
        command,
    )

    parsed = parse_update(update) if isinstance(update, dict) else None
    if parsed is not None:
        user = db.scalar(select(User).where(User.telegram_id == parsed.telegram_id))
        # Phone collection is optional and non-blocking - users can update their phone
        # later via natural conversation (e.g., "my phone number is +1 234 567 8901")

        if user is None and command != "/start":
            try:
                welcome_text = (
                    "Welcome! Please send /start to register, then try again."
                )
                telegram_message_id = send_message(
                    chat_id=parsed.chat_id,
                    text=welcome_text,
                    settings=settings,
                )
                # Log the message - create a closed session for it
                now = dt.datetime.now(dt.UTC)
                closed_session = TelegramSessions(
                    chat_id=parsed.chat_id,
                    started_at=now,
                    last_activity_at=now,
                    flush_at=now,
                    status="closed",
                    hint_command=None,
                    closed_at=now,
                    ack_sent_at=None,
                )
                db.add(closed_session)
                db.flush()
                record_outgoing_message(
                    db=db,
                    session_id=closed_session.id,
                    chat_id=parsed.chat_id,
                    kind="reply",
                    text=welcome_text,
                    telegram_message_id=telegram_message_id,
                    llm_call_id=None,
                )
                db.commit()
            except Exception as exc:
                logger.exception(
                    "telegram_unregistered_user_prompt_failed",
                    extra={"error": repr(exc), "chat_id": parsed.chat_id},
                )
                db.rollback()
            return

    # Handle confirm/cancel commands for pending DB actions
    if command in {"/confirm", "/cancel"}:
        parsed = parse_update(update) if isinstance(update, dict) else None
        if parsed is None:
            return

        user = db.scalar(select(User).where(User.telegram_id == parsed.telegram_id))
        if user is None:
            try:
                _send_and_log_message(
                    db=db,
                    chat_id=parsed.chat_id,
                    text="Please send /start to register first.",
                    kind="reply",
                    settings=settings,
                )
            except Exception as exc:
                logger.exception(
                    "telegram_confirm_cancel_unregistered_failed",
                    extra={"error": repr(exc), "chat_id": parsed.chat_id},
                )
            return

        pending_service = DBPendingActionService(db)
        pending_action = pending_service.get_latest_pending(user_id=user.id)
        if pending_action is None:
            try:
                _send_and_log_message(
                    db=db,
                    chat_id=parsed.chat_id,
                    text="No pending action to confirm or cancel.",
                    kind="reply",
                    settings=settings,
                )
            except Exception as exc:
                logger.exception(
                    "telegram_confirm_cancel_no_pending_failed",
                    extra={"error": repr(exc), "chat_id": parsed.chat_id},
                )
            return

        now = dt.datetime.now(dt.UTC)
        if pending_action.expires_at <= now:
            try:
                pending_service.cancel(pending=pending_action, cancelled_at=now)
                _send_and_log_message(
                    db=db,
                    chat_id=parsed.chat_id,
                    text="That action has expired. Please start over.",
                    kind="reply",
                    settings=settings,
                )
            except Exception as exc:
                logger.exception(
                    "telegram_confirm_cancel_expired_failed",
                    extra={"error": repr(exc), "chat_id": parsed.chat_id},
                )
            return

        if command == "/confirm":
            try:
                # Re-validate the pending action before executing
                action, errors = parse_db_action(pending_action.action_json)
                if action is None:
                    raise ValueError(f"pending_action_invalid: {errors}")

                # Compute per-restaurant roles
                rows = RestaurantService(db).list_for_user(user_id=user.id)
                restaurant_roles = {
                    str(membership.restaurant_id): membership.role
                    for _r, membership in rows
                }
                actor_role = (
                    ROLE_OWNER
                    if any(role == ROLE_OWNER for role in restaurant_roles.values())
                    else ROLE_STAFF
                )

                normalized_result = normalize_db_action(
                    action=action,
                    actor_user_id=str(user.id),
                    actor_role=actor_role,
                    restaurant_roles=restaurant_roles,
                )
                if normalized_result.normalized is None:
                    raise ValueError(
                        f"pending_action_not_allowed: {normalized_result.reasons}"
                    )

                validated = validate_db_action(
                    action=normalized_result.normalized,
                    actor_user_id=str(user.id),
                    actor_role=actor_role,
                    restaurant_roles=restaurant_roles,
                )
                if not validated.allowed:
                    raise ValueError(f"pending_action_not_allowed: {validated.reasons}")

                pending_service.confirm(pending=pending_action, confirmed_at=now)
                result = execute_confirmed_action(
                    session=db, pending_action=pending_action, now=now
                )
                success_msg = f"Done! {result.get('status', 'Action completed')}."
                _send_and_log_message(
                    db=db,
                    chat_id=parsed.chat_id,
                    text=success_msg,
                    kind="reply",
                    settings=settings,
                )
                logger.info(
                    "telegram_action_confirmed_and_executed update_id=%s chat_id=%s pending_id=%s",
                    update_id,
                    parsed.chat_id,
                    str(pending_action.id),
                )
            except ValueError as exc:
                error_msg = str(exc)
                if "not_confirmed" in error_msg:
                    error_msg = "That action couldn't be confirmed. Please try again."
                elif "expired" in error_msg:
                    error_msg = "That action has expired. Please start over."
                else:
                    error_msg = "Failed to execute that action. Please try again."
                try:
                    _send_and_log_message(
                        db=db,
                        chat_id=parsed.chat_id,
                        text=error_msg,
                        kind="reply",
                        settings=settings,
                    )
                except Exception as send_exc:
                    logger.exception(
                        "telegram_confirm_error_send_failed",
                        extra={"error": repr(send_exc), "chat_id": parsed.chat_id},
                    )
                logger.exception(
                    "telegram_action_confirm_failed",
                    extra={
                        "error": repr(exc),
                        "chat_id": parsed.chat_id,
                        "pending_id": str(pending_action.id),
                    },
                )
            except Exception as exc:
                try:
                    _send_and_log_message(
                        db=db,
                        chat_id=parsed.chat_id,
                        text="Failed to execute that action. Please try again.",
                        kind="reply",
                        settings=settings,
                    )
                except Exception as send_exc:
                    logger.exception(
                        "telegram_confirm_error_send_failed",
                        extra={"error": repr(send_exc), "chat_id": parsed.chat_id},
                    )
                logger.exception(
                    "telegram_action_confirm_failed",
                    extra={
                        "error": repr(exc),
                        "chat_id": parsed.chat_id,
                        "pending_id": str(pending_action.id),
                    },
                )
        elif command == "/cancel":
            try:
                pending_service.cancel(pending=pending_action, cancelled_at=now)
                _send_and_log_message(
                    db=db,
                    chat_id=parsed.chat_id,
                    text="Action cancelled.",
                    kind="reply",
                    settings=settings,
                )
                logger.info(
                    "telegram_action_cancelled update_id=%s chat_id=%s pending_id=%s",
                    update_id,
                    parsed.chat_id,
                    str(pending_action.id),
                )
            except Exception as exc:
                logger.exception(
                    "telegram_action_cancel_failed",
                    extra={
                        "error": repr(exc),
                        "chat_id": parsed.chat_id,
                        "pending_id": str(pending_action.id),
                    },
                )
                try:
                    _send_and_log_message(
                        db=db,
                        chat_id=parsed.chat_id,
                        text="Failed to cancel that action.",
                        kind="reply",
                        settings=settings,
                    )
                except Exception as send_exc:
                    logger.exception(
                        "telegram_cancel_error_send_failed",
                        extra={"error": repr(send_exc), "chat_id": parsed.chat_id},
                    )

        try:
            _persist_update_to_closed_session(update=update, db=db, user=user)
        except Exception as exc:
            logger.exception(
                "telegram_confirm_cancel_persist_failed",
                extra={"error": repr(exc), "chat_id": parsed.chat_id},
            )
            db.rollback()

        return

    if command in FORCE_FLUSH_COMMANDS:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat_id = (message.get("chat") or {}).get("id")
        if not isinstance(chat_id, int):
            return

        sealed_id = seal_open_session(chat_id=chat_id, session=db)
        logger.info(
            "telegram_force_flush_command update_id=%s chat_id=%s command=%s sealed_session_id=%s",
            update_id,
            chat_id,
            command,
            str(sealed_id) if sealed_id is not None else None,
        )
        if sealed_id is None:
            try:
                _send_and_log_message(
                    db=db,
                    chat_id=chat_id,
                    text=NOTHING_TO_PROCESS_REPLY_TEXT,
                    kind="reply",
                    settings=settings,
                )
            except Exception as exc:
                logger.exception(
                    "telegram_force_flush_nothing_to_process_reply_failed",
                    extra={"error": repr(exc), "chat_id": chat_id},
                )
            return

        _ensure_user_exists_for_update(update, db)
        _persist_update_to_session(update=update, db=db, session_id=sealed_id)

        from app.workers.tasks import process_session  # imported lazily

        logger.info(
            "telegram_force_flush_enqueuing_process_session update_id=%s chat_id=%s session_id=%s",
            update_id,
            chat_id,
            str(sealed_id),
        )
        cast(CeleryDelayable, process_session).delay(session_id=str(sealed_id))

        try:
            telegram_message_id = send_message(
                chat_id=chat_id, text=FORCE_FLUSH_REPLY_TEXT, settings=settings
            )
            if sealed_id is not None:
                record_outgoing_message(
                    db=db,
                    session_id=sealed_id,
                    chat_id=chat_id,
                    kind="reply",
                    text=FORCE_FLUSH_REPLY_TEXT,
                    telegram_message_id=telegram_message_id,
                    llm_call_id=None,
                )
                db.commit()
        except Exception as exc:
            logger.exception(
                "telegram_force_flush_reply_failed",
                extra={
                    "error": repr(exc),
                    "chat_id": chat_id,
                    "session_id": str(sealed_id),
                },
            )

        return

    if command in INSTANT_COMMANDS and command != "/start":
        _ensure_user_exists_for_update(update, db)
        session_id = ingest_update(
            update=update, session=db, settings=settings, schedule_flush=False
        )
        logger.info(
            "telegram_instant_command_ingested update_id=%s chat_id=%s command=%s session_id=%s",
            update_id,
            chat_id,
            command,
            str(session_id) if session_id is not None else None,
        )

    if settings.telegram_batching_enabled and not _is_instant_command(update):
        logger.warning(
            "telegram_batched_message_routed_to_worker update_id=%s chat_id=%s command=%s",
            update_id,
            chat_id,
            command,
        )
        return

    response = process_update(update=update, session=db, settings=settings)
    if response is None:
        logger.info(
            "telegram_process_update_noop update_id=%s chat_id=%s",
            update_id,
            chat_id,
        )
        return

    method = response.get("method")
    if method != "sendMessage":
        logger.warning(
            "telegram_handler_unsupported_response_method",
            extra={"method": method, "update_id": update.get("update_id")},
        )
        return

    chat_id = response.get("chat_id")
    text = response.get("text")
    if not isinstance(chat_id, int) or not isinstance(text, str):
        logger.warning(
            "telegram_handler_invalid_send_message_payload",
            extra={"payload": response, "update_id": update.get("update_id")},
        )
        return

    telegram_message_id = send_message(chat_id=chat_id, text=text, settings=settings)
    logger.info(
        "telegram_send_message_dispatched update_id=%s chat_id=%s",
        update_id,
        chat_id,
    )

    if command == "/start":
        session_id = ingest_update(
            update=update, session=db, settings=settings, schedule_flush=False
        )
        logger.info(
            "telegram_start_ingested update_id=%s chat_id=%s session_id=%s",
            update_id,
            chat_id,
            str(session_id) if session_id is not None else None,
        )

        # Log the outgoing message and close the session immediately
        if session_id is not None:
            record_outgoing_message(
                db=db,
                session_id=session_id,
                chat_id=chat_id,
                kind="reply",
                text=text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )
            # Close the session immediately since /start is an instant command
            now = dt.datetime.now(dt.UTC)
            db_session = db.scalar(
                select(TelegramSessions).where(TelegramSessions.id == session_id)
            )
            if db_session and db_session.status == "open":
                db_session.status = "closed"
                if db_session.closed_at is None:
                    db_session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session_id,
                        at=now,
                        event="session_closed_after_start_v0",
                        payload_json=None,
                        error=None,
                    )
                )
                db.commit()
        else:
            # If no session was created, create a closed session for logging
            parsed = parse_update(update)
            if parsed:
                now = dt.datetime.now(dt.UTC)
                closed_session = TelegramSessions(
                    chat_id=parsed.chat_id,
                    started_at=now,
                    last_activity_at=now,
                    flush_at=now,
                    status="closed",
                    hint_command=None,
                    closed_at=now,
                    ack_sent_at=None,
                )
                db.add(closed_session)
                db.flush()
                record_outgoing_message(
                    db=db,
                    session_id=closed_session.id,
                    chat_id=chat_id,
                    kind="reply",
                    text=text,
                    telegram_message_id=telegram_message_id,
                    llm_call_id=None,
                )
                db.commit()
    else:
        # For other instant commands, try to log to the session if it exists
        parsed = parse_update(update)
        if parsed:
            # Try to find or create a session for logging
            session_row = db.scalar(
                select(TelegramSessions)
                .where(
                    TelegramSessions.chat_id == parsed.chat_id,
                    TelegramSessions.status == "open",
                )
                .order_by(TelegramSessions.started_at.desc())
                .limit(1)
            )
            if session_row is None:
                # Create a closed session for logging
                now = dt.datetime.now(dt.UTC)
                session_row = TelegramSessions(
                    chat_id=parsed.chat_id,
                    started_at=now,
                    last_activity_at=now,
                    flush_at=now,
                    status="closed",
                    hint_command=None,
                    closed_at=now,
                    ack_sent_at=None,
                )
                db.add(session_row)
                db.flush()

            record_outgoing_message(
                db=db,
                session_id=session_row.id,
                chat_id=chat_id,
                kind="reply",
                text=text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )
            db.commit()
