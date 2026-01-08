"""
Session processing tasks for Celery.

Provides the process_session task for handling Telegram sessions
and the close_stale_sessions cleanup task.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import cast

from sqlalchemy import func, select

from app.ai.openai_client import OpenAIError
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.processing.session_processor import process_session as process_session_impl
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryApplyAsync, CeleryDelayable
from app.workers.db import worker_db_session
from app.workers.utils import _coerce_utc, _get_task_id, _parse_uuid

logger = logging.getLogger(__name__)


@celery_app.task(name="close_stale_sessions")
def close_stale_sessions() -> None:
    """
    Periodic task to ensure all sessions are closed.
    Closes sessions that have been open for more than 30 minutes.
    """
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=close_stale_sessions task_id=%s",
        task_id,
    )

    now = dt.datetime.now(dt.UTC)
    stale_threshold = now - dt.timedelta(minutes=30)
    stuck_processing_threshold = now - dt.timedelta(hours=1)

    with worker_db_session() as db:
        # Find all open sessions older than 30 minutes
        stale_open_sessions = (
            db.execute(
                select(TelegramSessions)
                .where(
                    TelegramSessions.status == "open",
                    TelegramSessions.started_at < stale_threshold,
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )

        closed_count = 0
        processed_count = 0

        for session in stale_open_sessions:
            # Check if session has messages
            message_count = db.scalar(
                select(func.count(TelegramMessages.id)).where(
                    TelegramMessages.session_id == session.id
                )
            )

            if message_count and message_count > 0:
                # Session has messages - process it
                session.status = "processing"
                session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session.id,
                        at=now,
                        event="session_force_closed_stale_v0",
                        payload_json=json.dumps(
                            {
                                "message_count": message_count,
                                "age_minutes": (
                                    now - _coerce_utc(session.started_at)
                                ).total_seconds()
                                / 60,
                            },
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )
                db.commit()
                # Enqueue processing
                cast(CeleryDelayable, process_session).delay(
                    session_id=str(session.id)
                )
                processed_count += 1
                logger.info(
                    "close_stale_sessions_processing session_id=%s chat_id=%s message_count=%s age_minutes=%.1f",
                    str(session.id),
                    session.chat_id,
                    message_count,
                    (now - _coerce_utc(session.started_at)).total_seconds() / 60,
                )
            else:
                # Session has no messages - just close it
                session.status = "closed"
                session.closed_at = now
                db.add(
                    ProcessingEvents(
                        session_id=session.id,
                        at=now,
                        event="session_force_closed_empty_v0",
                        payload_json=json.dumps(
                            {
                                "age_minutes": (
                                    now - _coerce_utc(session.started_at)
                                ).total_seconds()
                                / 60
                            },
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )
                db.commit()
                closed_count += 1
                logger.info(
                    "close_stale_sessions_closed session_id=%s chat_id=%s age_minutes=%.1f",
                    str(session.id),
                    session.chat_id,
                    (now - _coerce_utc(session.started_at)).total_seconds() / 60,
                )

        # Also check for stuck processing sessions (older than 1 hour)
        stuck_processing_sessions = (
            db.execute(
                select(TelegramSessions)
                .where(
                    TelegramSessions.status == "processing",
                    TelegramSessions.started_at < stuck_processing_threshold,
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )

        for session in stuck_processing_sessions:
            # Force close stuck processing sessions
            session.status = "closed"
            if session.closed_at is None:
                session.closed_at = now
            db.add(
                ProcessingEvents(
                    session_id=session.id,
                    at=now,
                    event="session_force_closed_stuck_processing_v0",
                    payload_json=json.dumps(
                        {
                            "age_hours": (
                                now - _coerce_utc(session.started_at)
                            ).total_seconds()
                            / 3600
                        },
                        ensure_ascii=False,
                    ),
                    error=None,
                )
            )
            db.commit()
            closed_count += 1
            logger.warning(
                "close_stale_sessions_stuck_processing session_id=%s chat_id=%s age_hours=%.1f",
                str(session.id),
                session.chat_id,
                (now - _coerce_utc(session.started_at)).total_seconds() / 3600,
            )

        logger.info(
            "close_stale_sessions_completed task_id=%s open_processed=%s empty_closed=%s stuck_closed=%s total=%s",
            task_id,
            processed_count,
            closed_count - len(stuck_processing_sessions),
            len(stuck_processing_sessions),
            processed_count + closed_count,
        )


@celery_app.task(name="process_session")
def process_session(*, session_id: str) -> None:
    """
    Process a Telegram session using intent classification.

    This task is called to process messages that have been collected
    in a session. It uses the intent-driven processor to classify
    the user's intent and execute the appropriate operation.
    """
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=process_session task_id=%s session_id=%s",
        task_id,
        session_id,
    )
    try:
        process_session_impl(session_id=session_id, task_id=task_id)
    except OpenAIError as exc:
        session_uuid = _parse_uuid(session_id)
        with worker_db_session() as db:
            failures = db.scalar(
                select(func.count())
                .select_from(ProcessingEvents)
                .where(
                    ProcessingEvents.session_id == session_uuid,
                    ProcessingEvents.event == "assistant_reply_attempt_failed_v0",
                )
            )
            failure_count = int(failures or 0)
            delay = min(300, 5 * (2 ** max(0, failure_count - 1)))
            delay = max(5, delay)

            last_scheduled = db.execute(
                select(ProcessingEvents)
                .where(
                    ProcessingEvents.session_id == session_uuid,
                    ProcessingEvents.event == "assistant_reply_retry_scheduled_v0",
                )
                .order_by(ProcessingEvents.at.desc())
                .limit(1)
            ).scalar_one_or_none()

            now = dt.datetime.now(dt.UTC)
            if (
                last_scheduled is not None
                and isinstance(last_scheduled.at, dt.datetime)
                and (now - last_scheduled.at).total_seconds() < 5
            ):
                logger.warning(
                    "process_session_retry_already_scheduled task_id=%s session_id=%s",
                    task_id,
                    session_id,
                )
                return

            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=now,
                    event="assistant_reply_retry_scheduled_v0",
                    payload_json=json.dumps({"delay_seconds": delay}),
                    error=str(exc),
                )
            )
            db.commit()

        logger.warning(
            "process_session_scheduling_retry task_id=%s session_id=%s delay_seconds=%s error=%s",
            task_id,
            session_id,
            delay,
            str(exc),
        )
        cast(CeleryApplyAsync, process_session).apply_async(
            kwargs={"session_id": session_id}, countdown=float(delay)
        )
        return
    except Exception as exc:
        # Catch-all: ensure session is closed even on unexpected errors
        session_uuid = _parse_uuid(session_id)
        try:
            with worker_db_session() as db:
                db_session = db.execute(
                    select(TelegramSessions)
                    .where(TelegramSessions.id == session_uuid)
                    .with_for_update()
                ).scalar_one_or_none()
                if db_session and db_session.status != "closed":
                    now = dt.datetime.now(dt.UTC)
                    db_session.status = "closed"
                    if db_session.closed_at is None:
                        db_session.closed_at = now
                    db.add(
                        ProcessingEvents(
                            session_id=session_uuid,
                            at=now,
                            event="session_closed_on_error_v0",
                            payload_json=json.dumps(
                                {"error": str(exc)}, ensure_ascii=False
                            ),
                            error=str(exc),
                        )
                    )
                    db.commit()
                    logger.error(
                        "process_session_error_closed_session task_id=%s session_id=%s error=%s",
                        task_id,
                        session_id,
                        str(exc),
                    )
        except Exception as close_exc:
            logger.exception(
                "process_session_failed_to_close_on_error task_id=%s session_id=%s original_error=%s close_error=%s",
                task_id,
                session_id,
                str(exc),
                str(close_exc),
            )
        raise

    logger.info(
        "process_session_completed task_id=%s session_id=%s",
        task_id,
        session_id,
    )
