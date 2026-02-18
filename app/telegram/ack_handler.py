"""
Shared ACK handler for both Telegram webhook and test endpoints.

Ensures identical behavior across production and testing.
Static ACK only — LLM-generated ACK will be re-added in Phase 5.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.telegram_session import TelegramSessions
from app.telegram.bot_api import bind_outlet_badge, send_message

logger = logging.getLogger(__name__)


def send_instant_ack(
    *,
    db: Session,
    settings: Settings,
    update: dict[str, Any],
    console_mode: bool = False,
) -> str | None:
    """
    Send instant ACK if message is substantial (>10 chars or has file).

    Creates a session record for telemetry and returns session_id to be
    added to the update dict for the Celery worker.

    Returns:
        session_id string, or None if no ACK sent.
    """
    if not isinstance(update, dict):
        return None

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    chat_dict = message.get("chat")
    if not isinstance(chat_dict, dict):
        return None

    chat_id_raw = chat_dict.get("id")
    if not isinstance(chat_id_raw, int):
        return None

    chat_id = chat_id_raw

    message_text = (message.get("text") or message.get("caption") or "").strip()
    has_file = bool(
        message.get("document")
        or message.get("photo")
        or message.get("voice")
        or message.get("video")
        or message.get("audio")
    )

    if not (message_text or has_file):
        return None

    now = dt.datetime.now(dt.UTC)
    session_row = TelegramSessions(
        chat_id=chat_id,
        started_at=now,
        last_activity_at=now,
        status="closed",
        closed_at=now,
        ack_sent_at=None,
    )
    db.add(session_row)
    db.commit()
    session_id_str = str(session_row.id)

    ack_text = "Got it. File received." if has_file else "Got it..."

    if chat_id != 0 and not console_mode:
        try:
            with bind_outlet_badge("-"):
                send_message(chat_id=chat_id, text=ack_text, settings=settings)
            logger.info("ack_sent chat_id=%s", chat_id)
        except Exception as exc:
            logger.warning("ack_send_failed chat_id=%s error=%r", chat_id, exc)
    else:
        logger.info("ack_console_mode ack=%r has_file=%s", ack_text, has_file)

    return session_id_str
