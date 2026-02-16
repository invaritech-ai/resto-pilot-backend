"""
Shared ACK handler for both Telegram webhook and test endpoints.

Ensures identical behavior across production and testing.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai.ack import generate_ack
from app.core.config import Settings
from app.db.models.telegram_session import TelegramSessions
from app.telegram.bot_api import send_message
from app.workers.telemetry import record_llm_call

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

    Creates session for telemetry and returns session_id to be added to update.

    Args:
        db: Database session
        settings: App settings
        update: Telegram update dict
        console_mode: If True, log ACK instead of sending to Telegram

    Returns:
        session_id string to add to update, or None if no ACK sent
    """
    if not isinstance(update, dict):
        return None

    # Extract message and chat_id from update
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

    # Extract message text and detect files
    message_text = (message.get("text") or message.get("caption") or "").strip()
    has_file = bool(
        message.get("document")
        or message.get("photo")
        or message.get("voice")
        or message.get("video")
        or message.get("audio")
    )

    # Only send ACK if message is substantial
    if not (len(message_text) > 10 or has_file):
        return None

    # Create session for ACK telemetry logging
    now = dt.datetime.now(dt.UTC)
    session_row = TelegramSessions(
        chat_id=chat_id,
        started_at=now,
        last_activity_at=now,
        flush_at=now,
        status="closed",
        hint_command=None,
        closed_at=now,
        ack_sent_at=None,
    )
    db.add(session_row)
    db.commit()
    session_id_str = str(session_row.id)

    # Generate ACK with LLM for variety
    ack_text, ack_telemetry = generate_ack(
        settings=settings,
        message_text=message_text,
        has_file=has_file,
    )

    # Log ACK LLM call if successful
    if ack_telemetry:
        try:
            record_llm_call(
                db=db,
                session_id=session_row.id,
                chat_id=chat_id,
                purpose="ack",
                model=ack_telemetry.get("model", ""),
                openrouter_generation_id=ack_telemetry.get("generation_id"),
                usage=ack_telemetry.get("usage", {}),
                latency_ms=ack_telemetry.get("latency_ms"),
            )
            db.commit()
        except Exception as exc:
            logger.warning("ack_telemetry_log_failed error=%r", exc)

    # Fallback to static ACK if LLM failed
    if not ack_text:
        ack_text = "Got it. File received." if has_file else "Got it..."

    # Send ACK (or log in console mode)
    if chat_id != 0 and not console_mode:
        # Real Telegram chat
        try:
            send_message(chat_id=chat_id, text=ack_text, settings=settings)
            logger.info("ack_sent chat_id=%s ack=%r", chat_id, ack_text)
        except Exception as exc:
            # ACK failure shouldn't block processing
            logger.warning("ack_send_failed chat_id=%s error=%r", chat_id, exc)
    else:
        # Console mode (chat_id=0 or console_mode=True)
        logger.info(
            "ack_console_mode ack=%r message_len=%s has_file=%s",
            ack_text,
            len(message_text),
            has_file,
        )

    return session_id_str
