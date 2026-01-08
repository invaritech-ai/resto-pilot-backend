"""
Session processor - now redirects to the intent-driven processor.

This module is kept for backward compatibility with existing session tasks.
The main processing logic has moved to app.conversation.processor.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.ai.intent_classifier import classify_intent
from app.ai.openai_client import OpenAIError
from app.conversation import responses
from app.conversation.context import load_context, update_context_from_result, get_context_for_classifier
from app.conversation.executor import execute_intent
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.bot_api import send_message
from app.workers.db import worker_db_session
from app.workers.telemetry import record_llm_call, record_outgoing_message

logger = logging.getLogger(__name__)


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _get_message_text(messages: list[TelegramMessages]) -> str:
    """Extract text from telegram messages."""
    parts = []
    for msg in messages:
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
    return "\n".join(parts).strip()


def _has_file(messages: list[TelegramMessages]) -> tuple[bool, str | None, str | None]:
    """Check if messages contain a file."""
    for msg in messages:
        if msg.file_id:
            return True, msg.file_kind, msg.file_id
    return False, None, None


def process_session(*, session_id: str, task_id: str | None = None) -> None:
    """
    Process a telegram session using the new intent-driven approach.

    This replaces the old agent-based processing with:
    1. Intent classification
    2. Parameter extraction
    3. Static operation execution
    4. Template response

    Args:
        session_id: Session UUID string
        task_id: Optional task ID for logging
    """
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
        # Load session with lock
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

        # Check if reply already sent
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
            _close_session(db, db_session, session_uuid)
            return

        # Load messages
        messages = list(
            db.scalars(
                select(TelegramMessages)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(
                    TelegramMessages.received_at.asc(),
                    TelegramMessages.message_id.asc(),
                )
            )
        )

        if not messages:
            logger.info(
                "process_session_noop_no_messages task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            _close_session(db, db_session, session_uuid)
            return

        # Get user
        user = db.scalar(select(User).where(User.id == messages[0].user_id))
        if user is None:
            logger.warning(
                "process_session_user_not_found task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            _close_session(db, db_session, session_uuid)
            return

        settings = get_settings()
        message_text = _get_message_text(messages)
        has_file, file_kind, file_id = _has_file(messages)

        logger.info(
            "process_session_loaded task_id=%s session_id=%s message_count=%s has_file=%s",
            task_id,
            session_id,
            len(messages),
            has_file,
        )

        # Load user context
        context = load_context(db, user)
        classifier_context = get_context_for_classifier(context)

        # Classify intent
        classified = classify_intent(
            message_text=message_text or "",
            settings=settings,
            has_file=has_file,
            file_kind=file_kind,
            context=classifier_context,
        )

        # Record LLM call for telemetry
        if classified.usage:
            record_llm_call(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                purpose="intent_classification",
                model=classified.model,
                openrouter_generation_id=classified.generation_id,
                upstream_id=None,
                provider_name=None,
                usage=classified.usage,
                latency_ms=classified.latency_ms,
                total_cost_usd=None,
                error=None,
            )
            db.commit()

        logger.info(
            "process_session_intent_classified task_id=%s session_id=%s intent=%s confidence=%s",
            task_id,
            session_id,
            classified.intent.value,
            classified.confidence,
        )

        # Execute intent
        try:
            result = execute_intent(
                classified=classified,
                db=db,
                user=user,
                context=context,
                settings=settings,
            )
            response_text = result.response

            # Update context
            if result.context_update:
                update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update=result.context_update,
                )

        except Exception as exc:
            logger.exception(
                "process_session_execution_failed task_id=%s session_id=%s",
                task_id,
                session_id,
                extra={"error": str(exc)},
            )
            response_text = responses.ERROR_GENERIC

        # Send response
        try:
            telegram_message_id = send_message(
                chat_id=db_session.chat_id,
                text=response_text,
                settings=settings,
            )

            record_outgoing_message(
                db=db,
                session_id=session_uuid,
                chat_id=db_session.chat_id,
                kind="reply",
                text=response_text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )

            # Record sent event
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_sent_v0",
                    payload_json=None,
                    error=None,
                )
            )
            db.commit()

            logger.info(
                "process_session_response_sent task_id=%s session_id=%s",
                task_id,
                session_id,
            )

        except Exception as exc:
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_send_failed_v0",
                    payload_json=None,
                    error=str(exc),
                )
            )
            db.commit()
            raise OpenAIError(f"Failed to send response: {exc}") from exc

        # Close session
        _close_session(db, db_session, session_uuid)

        logger.info(
            "process_session_completed task_id=%s session_id=%s",
            task_id,
            session_id,
        )


def _close_session(
    db: DBSession,
    db_session: TelegramSessions,
    session_uuid: uuid.UUID,
) -> None:
    """Close the session."""
    now = dt.datetime.now(dt.UTC)
    if db_session.status != "closed":
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
