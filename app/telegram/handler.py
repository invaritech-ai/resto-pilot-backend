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


def handle_update(update: dict, db: Session, settings: Settings) -> None:
    """
    Process a Telegram update with the appropriate handler based on settings.

    This function encapsulates the routing logic that determines whether to
    use batching mode (ingest_update) or immediate processing (process_update).

    The webhook endpoint should always return quickly; any responses to users
    are sent via the Telegram Bot API from background workers.

    Args:
        update: The Telegram update dictionary from the webhook
        db: Database session for persistence operations
        settings: Application settings including batching configuration
    """
    logger.info("handle_update_received", extra={"update_id": update.get("update_id")})

    if settings.telegram_batching_enabled:
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
