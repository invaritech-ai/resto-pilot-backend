from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import cast

from celery import current_task
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.telegram.handler import handle_update
from app.telegram.bot_api import send_message
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryDelayable
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)

SESSION_FLUSH_REPLY_TEXT = "Got it — I'm on it."


def _coerce_utc(value: dt.datetime) -> dt.datetime:
    """
    Normalize datetimes to UTC-aware.

    SQLite may return naive datetimes even when columns are declared with
    timezone=True; treat naive values as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _get_task_id() -> str | None:
    request = getattr(current_task, "request", None)
    return getattr(request, "id", None)


@celery_app.task(name="flush_session")
def flush_session(*, session_id: str, expected_last_activity_at: str) -> None:
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=flush_session task_id=%s session_id=%s",
        task_id,
        session_id,
    )
    expected = dt.datetime.fromisoformat(expected_last_activity_at)
    session_uuid = _parse_uuid(session_id)
    chat_id: int | None = None

    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None:
            logger.info(
                "flush_session_noop_session_not_found task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return
        if db_session.status != "open":
            logger.info(
                "flush_session_noop_session_not_open task_id=%s session_id=%s status=%s",
                task_id,
                session_id,
                db_session.status,
            )
            return
        if _coerce_utc(db_session.last_activity_at) != _coerce_utc(expected):
            logger.info(
                "flush_session_noop_stale_expected_last_activity task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return

        chat_id = db_session.chat_id
        now = dt.datetime.now(dt.UTC)
        db_session.status = "processing"
        db_session.closed_at = now
        if db_session.ack_sent_at is None:
            db_session.ack_sent_at = now
        db.add(
            ProcessingEvents(
                session_id=db_session.id,
                at=now,
                event="session_flushed",
                payload_json=None,
                error=None,
            )
        )
        db.commit()

    logger.info(
        "flush_session_enqueuing_process_session task_id=%s session_id=%s",
        task_id,
        session_id,
    )
    cast(CeleryDelayable, process_session).delay(session_id=session_id)
    if isinstance(chat_id, int):
        settings = get_settings()
        try:
            send_message(chat_id=chat_id, text=SESSION_FLUSH_REPLY_TEXT, settings=settings)
        except Exception as exc:
            logger.exception(
                "telegram_flush_reply_failed",
                extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
            )


@celery_app.task(name="send_session_ack")
def send_session_ack(*, session_id: str, text: str = SESSION_FLUSH_REPLY_TEXT) -> None:
    task_id = _get_task_id()
    session_uuid = _parse_uuid(session_id)

    logger.info(
        "celery_task_started name=send_session_ack task_id=%s session_id=%s",
        task_id,
        session_id,
    )

    chat_id: int | None = None
    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None:
            logger.info(
                "send_session_ack_noop_session_not_found task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return
        if db_session.ack_sent_at is not None:
            logger.info(
                "send_session_ack_noop_already_sent task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return
        now = dt.datetime.now(dt.UTC)
        db_session.ack_sent_at = now
        chat_id = db_session.chat_id
        db.commit()

    if not isinstance(chat_id, int):
        return

    settings = get_settings()
    try:
        send_message(chat_id=chat_id, text=text, settings=settings)
    except Exception as exc:
        logger.exception(
            "telegram_ack_send_failed",
            extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
        )


@celery_app.task(name="process_session")
def process_session(*, session_id: str) -> None:
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=process_session task_id=%s session_id=%s",
        task_id,
        session_id,
    )
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
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

        messages = list(
            db.scalars(
                select(TelegramMessages)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(
                    TelegramMessages.received_at.asc(), TelegramMessages.message_id.asc()
                )
            )
        )

        hint = db_session.hint_command
        file_kinds = [m.file_kind for m in messages if m.file_kind]
        mime_types = [m.mime for m in messages if m.mime]

        logger.info(
            "process_session_loaded task_id=%s session_id=%s status=%s message_count=%s hint=%s",
            task_id,
            session_id,
            db_session.status,
            len(messages),
            hint,
        )

        routing_plan = {
            "hint_command": hint,
            "message_count": len(messages),
            "file_kinds": file_kinds,
            "mime_types": mime_types,
        }

        now = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=now,
                event="router_plan_v0",
                payload_json=json.dumps(routing_plan),
                error=None,
            )
        )

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

    logger.info(
        "process_session_completed task_id=%s session_id=%s",
        task_id,
        session_id,
    )


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict) -> None:
    """
    Background task to handle Telegram updates without FastAPI request context.
    
    Args:
        update: The Telegram update dictionary from the webhook
    """
    task_id = _get_task_id()
    update_id = update.get("update_id") if isinstance(update, dict) else None
    message = (
        (update.get("message") or update.get("edited_message") or {})
        if isinstance(update, dict)
        else {}
    )
    chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None

    logger.info(
        "celery_task_started name=handle_telegram_update task_id=%s update_id=%s chat_id=%s",
        task_id,
        update_id,
        chat_id,
    )
    with worker_db_session() as db:
        settings = get_settings()
        try:
            handle_update(update=update, db=db, settings=settings)
        except Exception as exc:
            db.rollback()
            logger.exception(
                "telegram_update_processing_failed",
                extra={
                    "error": repr(exc),
                    "update_id": update.get("update_id"),
                    "message_id": (update.get("message") or {}).get("message_id"),
                    "chat_id": ((update.get("message") or {}).get("chat") or {}).get("id"),
                },
            )
            raise

    logger.info(
        "handle_telegram_update_completed task_id=%s update_id=%s chat_id=%s",
        task_id,
        update_id,
        chat_id,
    )
