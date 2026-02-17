"""
Telegram update handler.

Minimal stub - full bot processing will be rebuilt in Phase 5.
Infrastructure (session creation, message logging, history loading) is preserved.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.bot_api import send_message
from app.telegram.commands import extract_command
from app.telegram.ingest import parse_update
from app.telegram.processor import process_update as process_start_command
from app.services.telemetry import record_outgoing_message

logger = logging.getLogger(__name__)


def _extract_command_from_update(update: dict) -> tuple[str | None, str | None]:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None, None
    text = message.get("text") if isinstance(message.get("text"), str) else None
    caption = message.get("caption") if isinstance(message.get("caption"), str) else None
    return extract_command(text, caption)


def _format_incoming_content(message: TelegramMessages) -> str:
    parts = []
    if isinstance(message.text, str) and message.text.strip():
        parts.append(message.text.strip())
    if isinstance(message.caption, str) and message.caption.strip():
        parts.append(message.caption.strip())
    return "\n".join(parts).strip()


def _load_recent_history(
    *,
    db: Session,
    chat_id: int,
    limit: int = 20,
    before_received_at: dt.datetime | None = None,
) -> list[dict[str, str]]:
    incoming_query = select(TelegramMessages).where(TelegramMessages.chat_id == chat_id)
    if before_received_at is not None:
        incoming_query = incoming_query.where(TelegramMessages.received_at < before_received_at)
    incoming = db.scalars(
        incoming_query.order_by(TelegramMessages.received_at.desc()).limit(limit)
    ).all()

    outgoing = db.scalars(
        select(TelegramOutgoingMessages)
        .where(
            TelegramOutgoingMessages.chat_id == chat_id,
            TelegramOutgoingMessages.kind.in_(("reply", "start_reply")),
        )
        .order_by(TelegramOutgoingMessages.sent_at.desc())
        .limit(limit)
    ).all()

    items: list[tuple[dt.datetime, str, str]] = []
    for msg in incoming:
        content = _format_incoming_content(msg)
        if content:
            items.append((msg.received_at, "user", content))
    for msg in outgoing:
        content = msg.text.strip() if msg.text else ""
        if content:
            items.append((msg.sent_at, "assistant", content))

    items.sort(key=lambda row: row[0])
    return [{"role": role, "content": content} for _, role, content in items][-limit:]


def _create_session_and_message(
    *,
    db: Session,
    parsed: Any,
    user: User,
) -> tuple[TelegramSessions, TelegramMessages]:
    now = dt.datetime.now(dt.UTC)
    session = TelegramSessions(
        chat_id=parsed.chat_id,
        started_at=now,
        last_activity_at=now,
        status="closed",
        closed_at=now,
        ack_sent_at=None,
    )
    db.add(session)
    db.flush()

    message = TelegramMessages(
        session_id=session.id,
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
    db.add(message)
    db.flush()

    return session, message


def handle_update_v2(update: dict, db: Session, settings: Settings) -> None:
    """
    Process a Telegram update.

    TODO (Phase 5): Implement full bot processing with execution.py + db_tools.
    Currently: handles /start, logs all other messages.
    """
    update_id = update.get("update_id") if isinstance(update, dict) else None
    message = (update.get("message") or update.get("edited_message") or {}) if isinstance(update, dict) else {}
    chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None
    logger.info("handle_update_v2_received update_id=%s chat_id=%s", update_id, chat_id)

    parsed = parse_update(update) if isinstance(update, dict) else None
    if parsed is None:
        return

    command, _args = _extract_command_from_update(update)
    user_info = message.get("from") or {}
    telegram_id = user_info.get("id")
    if not isinstance(telegram_id, int):
        return

    user = db.scalar(select(User).where(User.telegram_id == telegram_id))

    # Handle /start
    if command == "/start":
        response = process_start_command(update=update, session=db, settings=settings)
        start_user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if start_user is not None:
            try:
                start_session, _ = _create_session_and_message(db=db, parsed=parsed, user=start_user)
                db.commit()
            except IntegrityError:
                db.rollback()
                start_session = None
        else:
            start_session = None
        if response and isinstance(response, dict):
            response_text = response.get("text", "")
            try:
                telegram_message_id = send_message(
                    chat_id=response.get("chat_id", parsed.chat_id),
                    text=response_text,
                    settings=settings,
                )
                if start_session is not None:
                    record_outgoing_message(
                        db=db,
                        session_id=start_session.id,
                        chat_id=parsed.chat_id,
                        kind="start_reply",
                        text=response_text,
                        telegram_message_id=telegram_message_id,
                        llm_call_id=None,
                    )
                    db.commit()
            except Exception as exc:
                logger.exception("handle_update_v2_start_reply_failed", extra={"error": repr(exc)})
        return

    if user is None:
        logger.warning("handle_update_v2_unregistered update_id=%s", update_id)
        return

    # Log the message (full processing TODO in Phase 5)
    try:
        session_id_str = update.get("_session_id") if isinstance(update, dict) else None
        if session_id_str:
            session_id = uuid.UUID(session_id_str)
            session = db.get(TelegramSessions, session_id)
            if session is None:
                session, _ = _create_session_and_message(db=db, parsed=parsed, user=user)
            else:
                tg_message = TelegramMessages(
                    session_id=session.id,
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
                db.add(tg_message)
                db.flush()
        else:
            session, _ = _create_session_and_message(db=db, parsed=parsed, user=user)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("handle_update_v2_duplicate update_id=%s", update_id)


# Alias for backwards compatibility
handle_update = handle_update_v2
