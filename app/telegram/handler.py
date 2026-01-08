"""
Simplified handler for Telegram updates - Intent-driven static bot.

This is the new handler that replaces the batched/agent-based approach
with instant processing via intent classification.

Key changes from handler.py:
- No batching - all messages processed immediately
- Intent classification instead of agent loop
- Simpler flow: ACK -> Classify -> Execute -> Respond
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.conversation import responses
from app.conversation.processor import process_message_instant, send_ack_message
from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.bot_api import send_message
from app.telegram.commands import extract_command
from app.telegram.ingest import parse_update
from app.telegram.processor import process_update as process_start_command
from app.workers.telemetry import record_outgoing_message

logger = logging.getLogger(__name__)


def _extract_command_from_update(update: dict) -> tuple[str | None, str | None]:
    """Extract command and arguments from update."""
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None, None

    text = message.get("text") if isinstance(message.get("text"), str) else None
    caption = (
        message.get("caption") if isinstance(message.get("caption"), str) else None
    )
    return extract_command(text, caption)


def _create_session_and_message(
    *,
    db: Session,
    parsed: any,
    user: User,
) -> tuple[TelegramSessions, TelegramMessages]:
    """Create a session and message record for telemetry."""
    now = dt.datetime.now(dt.UTC)
    session = TelegramSessions(
        chat_id=parsed.chat_id,
        started_at=now,
        last_activity_at=now,
        flush_at=now,
        status="closed",
        hint_command=None,
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
    Process a Telegram update with instant intent-driven processing.

    Flow:
    1. Parse update and get/create user
    2. Handle /start specially (onboarding)
    3. Send instant ACK
    4. Classify intent and execute
    5. Send response
    6. Record telemetry

    Args:
        update: The Telegram update dictionary from the webhook
        db: Database session for persistence operations
        settings: Application settings
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
        "handle_update_v2_received update_id=%s chat_id=%s",
        update_id,
        chat_id,
    )

    # Parse the update
    parsed = parse_update(update) if isinstance(update, dict) else None
    if parsed is None:
        logger.warning(
            "handle_update_v2_parse_failed update_id=%s",
            update_id,
        )
        return

    # Extract command
    command, args = _extract_command_from_update(update)

    # Get or create user
    user_info = message.get("from") or {}
    telegram_id = user_info.get("id")

    if not isinstance(telegram_id, int):
        logger.warning(
            "handle_update_v2_no_telegram_id update_id=%s",
            update_id,
        )
        return

    user = db.scalar(select(User).where(User.telegram_id == telegram_id))

    # Handle unregistered users
    if user is None and command != "/start":
        try:
            send_message(
                chat_id=parsed.chat_id,
                text=responses.ERROR_NOT_REGISTERED,
                settings=settings,
            )
        except Exception as exc:
            logger.exception(
                "handle_update_v2_unregistered_reply_failed",
                extra={"error": repr(exc), "chat_id": parsed.chat_id},
            )
        return

    # Handle /start command (onboarding) - use existing processor
    if command == "/start":
        # Process /start (creates user if needed, handles invite codes)
        response = process_start_command(update=update, session=db, settings=settings)

        # Get the user after processing (may have been created)
        start_user = db.scalar(select(User).where(User.telegram_id == telegram_id))

        # Create session and record incoming message for telemetry
        if start_user is not None:
            try:
                start_session, _start_message = _create_session_and_message(
                    db=db,
                    parsed=parsed,
                    user=start_user,
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                start_session = None
        else:
            start_session = None

        # Send response and record outgoing
        if response:
            response_text = response.get("text", "")
            response_chat_id = response.get("chat_id", parsed.chat_id)
            try:
                telegram_message_id = send_message(
                    chat_id=response_chat_id,
                    text=response_text,
                    settings=settings,
                )

                # Record outgoing message for telemetry
                if start_session is not None:
                    record_outgoing_message(
                        db=db,
                        session_id=start_session.id,
                        chat_id=response_chat_id,
                        kind="start_reply",
                        text=response_text,
                        telegram_message_id=telegram_message_id,
                        llm_call_id=None,
                    )
                    db.commit()

            except Exception as exc:
                logger.exception(
                    "handle_update_v2_start_reply_failed",
                    extra={"error": repr(exc), "chat_id": parsed.chat_id},
                )
        return

    # At this point, user must exist
    if user is None:
        logger.error(
            "handle_update_v2_user_not_found update_id=%s telegram_id=%s",
            update_id,
            telegram_id,
        )
        return

    # Create session and message for telemetry
    try:
        session, tg_message = _create_session_and_message(
            db=db,
            parsed=parsed,
            user=user,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning(
            "handle_update_v2_duplicate_message update_id=%s",
            update_id,
        )
        return

    # Check if message has a file
    has_file = bool(parsed.file_id)

    # Send instant ACK (non-blocking, best effort)
    # Skip ACK for very short interactions to reduce noise
    message_text = (parsed.text or "").strip()
    if len(message_text) > 10 or has_file:
        send_ack_message(
            chat_id=parsed.chat_id,
            settings=settings,
            has_file=has_file,
            db=db,
            session_id=session.id,
        )
        db.commit()

    # Process the message
    try:
        response_text = process_message_instant(
            db=db,
            user=user,
            messages=[tg_message],
            settings=settings,
            session_id=session.id,
        )
    except Exception as exc:
        logger.exception(
            "handle_update_v2_processing_failed",
            extra={
                "error": repr(exc),
                "chat_id": parsed.chat_id,
                "session_id": str(session.id),
            },
        )
        response_text = responses.ERROR_GENERIC

    # Send response
    try:
        telegram_message_id = send_message(
            chat_id=parsed.chat_id,
            text=response_text,
            settings=settings,
        )

        # Record outgoing message
        record_outgoing_message(
            db=db,
            session_id=session.id,
            chat_id=parsed.chat_id,
            kind="reply",
            text=response_text,
            telegram_message_id=telegram_message_id,
            llm_call_id=None,
        )
        db.commit()

        logger.info(
            "handle_update_v2_response_sent update_id=%s chat_id=%s session_id=%s",
            update_id,
            parsed.chat_id,
            str(session.id),
        )

    except Exception as exc:
        logger.exception(
            "handle_update_v2_response_send_failed",
            extra={
                "error": repr(exc),
                "chat_id": parsed.chat_id,
                "session_id": str(session.id),
            },
        )
