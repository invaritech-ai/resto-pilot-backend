"""
Unified handler for Telegram updates.

This module consolidates the routing and business logic for processing
Telegram updates, keeping the webhook route thin.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
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


def _extract_command(update: dict) -> str | None:
    """
    Extract the command from a Telegram update's text or caption.

    Returns the command (e.g. "/start") if present, or None.
    Commands are extracted from the first word if it starts with "/".
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    text = message.get("text") or message.get("caption") or ""
    if not isinstance(text, str):
        return None
    first_word = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if first_word.startswith("/"):
        # Handle commands with @botname suffix (e.g. "/start@mybot")
        return first_word.split("@")[0].lower()
    return None


def _is_instant_command(update: dict) -> bool:
    """Check if the update contains an instant command that bypasses batching."""
    command = _extract_command(update)
    return command is not None and command in INSTANT_COMMANDS


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
