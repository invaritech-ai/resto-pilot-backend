"""
Unified handler for Telegram updates.

This module consolidates the routing and business logic for processing
Telegram updates, keeping the webhook route thin.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.bot_api import send_message
from app.telegram.ingest import ingest_update
from app.telegram.processor import process_update

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


def _extract_command(update: dict) -> tuple[str | None, str | None]:
    """
    Extract the command from a Telegram update's text or caption.

    Returns a tuple of (command, args) where:
    - command is the normalized command (e.g. "/start") or None
    - args is the remainder of the text after the command (e.g. "CODE") or None

    Commands are extracted from the first token if it starts with "/".
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None, None

    raw_text = message.get("text")
    raw_caption = message.get("caption")
    content = raw_text if isinstance(raw_text, str) and raw_text.strip() else raw_caption
    if not isinstance(content, str):
        return None, None

    stripped = content.strip()
    if not stripped:
        return None, None

    parts = stripped.split(maxsplit=1)
    first_token = parts[0]
    rest = parts[1].strip() if len(parts) == 2 else None

    if not first_token.startswith("/"):
        return None, None

    # Handle commands with @botname suffix (e.g. "/start@mybot")
    command = first_token.split("@", 1)[0].lower()
    return command, (rest or None)


def _is_instant_command(update: dict) -> bool:
    """Check if the update contains an instant command that bypasses batching."""
    command, _args = _extract_command(update)
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

    command, _args = _extract_command(update)
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
