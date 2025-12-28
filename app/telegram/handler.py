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
from app.db.models.user import User
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.bot_api import send_message
from app.telegram.commands import extract_command
from app.telegram.ingest import ingest_update, parse_update, seal_open_session
from app.telegram.processor import process_update
from app.workers.celery_types import CeleryDelayable

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Reserved commands that bypass batching and are processed immediately.
# This is the single source of truth - add new instant commands here.
# -----------------------------------------------------------------------------
INSTANT_COMMANDS: frozenset[str] = frozenset({
    "/start",
    "/respond",
    "/done",
})

FORCE_FLUSH_COMMANDS: frozenset[str] = frozenset({
    "/respond",
    "/done",
})

FORCE_FLUSH_REPLY_TEXT = "Got it — I'm on it."
NOTHING_TO_PROCESS_REPLY_TEXT = "Nothing to process right now."


def _persist_update_to_session(*, update: dict, db: Session, session_id: uuid.UUID) -> None:
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
    caption = message.get("caption") if isinstance(message.get("caption"), str) else None
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

    The webhook endpoint should always return quickly; any responses to users
    are sent via the Telegram Bot API from background workers.

    Args:
        update: The Telegram update dictionary from the webhook
        db: Database session for persistence operations
        settings: Application settings including batching configuration
    """
    logger.info("handle_update_received", extra={"update_id": update.get("update_id")})

    command, _args = _extract_command_from_update(update)

    if command in FORCE_FLUSH_COMMANDS:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat_id = (message.get("chat") or {}).get("id")
        if not isinstance(chat_id, int):
            return

        sealed_id = seal_open_session(chat_id=chat_id, session=db)
        if sealed_id is None:
            try:
                send_message(
                    chat_id=chat_id, text=NOTHING_TO_PROCESS_REPLY_TEXT, settings=settings
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

        cast(CeleryDelayable, process_session).delay(session_id=str(sealed_id))

        try:
            send_message(chat_id=chat_id, text=FORCE_FLUSH_REPLY_TEXT, settings=settings)
        except Exception as exc:
            logger.exception(
                "telegram_force_flush_reply_failed",
                extra={"error": repr(exc), "chat_id": chat_id, "session_id": str(sealed_id)},
            )

        return

    if command in INSTANT_COMMANDS and command != "/start":
        _ensure_user_exists_for_update(update, db)
        ingest_update(update=update, session=db, settings=settings, schedule_flush=False)

    # Instant commands always bypass batching
    if settings.telegram_batching_enabled and not _is_instant_command(update):
        ingest_update(update=update, session=db, settings=settings)
        return

    response = process_update(update=update, session=db, settings=settings)
    if response is None:
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

    send_message(chat_id=chat_id, text=text, settings=settings)

    if command == "/start":
        ingest_update(update=update, session=db, settings=settings, schedule_flush=False)
