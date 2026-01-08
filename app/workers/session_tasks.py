from __future__ import annotations

import datetime as dt
import json
import logging
import random
from typing import cast

from sqlalchemy import func, select

from app.ai.backchannel import generate_backchannel_text, should_attempt_backchannel
from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_chat_states import TelegramChatStates
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.telegram_session import TelegramSessions
from app.processing.session_processor import process_session as process_session_impl
from app.telegram.bot_api import send_message
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryApplyAsync, CeleryDelayable
from app.workers.db import worker_db_session
from app.workers.telemetry import (
    record_llm_call,
    record_outgoing_message,
    schedule_openrouter_cost_backfill,
)
from app.workers.utils import (
    MESSAGE_BACKCHANNEL_KIND,
    SESSION_FLUSH_REPLY_TEXT,
    _coerce_utc,
    _get_task_id,
    _parse_uuid,
)

logger = logging.getLogger(__name__)


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

        now = dt.datetime.now(dt.UTC)
        db_session.status = "processing"
        db_session.closed_at = now
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
    # Policy: do not send a model-based "receipt ack" at flush time.
    # Per-message acks (best-effort, ~60%) are handled in ingest_update().
    cast(CeleryDelayable, process_session).delay(session_id=session_id)


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
    off_topic_mode = False
    recent_messages: list[TelegramMessages] = []
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
        chat_id = db_session.chat_id
        state = db.scalar(select(TelegramChatStates).where(TelegramChatStates.chat_id == chat_id))
        if state is not None:
            off_topic_mode = bool(state.off_topic_mode)
        already_backchanneled = (
            db.scalar(
                select(func.count())
                .select_from(TelegramOutgoingMessages)
                .where(
                    TelegramOutgoingMessages.session_id == session_uuid,
                    TelegramOutgoingMessages.kind == MESSAGE_BACKCHANNEL_KIND,
                )
            )
            or 0
        )
        if int(already_backchanneled) > 0:
            logger.info(
                "send_session_ack_noop_already_backchanneled task_id=%s session_id=%s chat_id=%s",
                task_id,
                session_id,
                chat_id,
            )
            if db_session.ack_sent_at is None:
                db_session.ack_sent_at = dt.datetime.now(dt.UTC)
                db.commit()
            return
        recent_messages = list(
            reversed(
                list(
                    db.scalars(
                        select(TelegramMessages)
                        .where(TelegramMessages.session_id == session_uuid)
                        .order_by(
                            TelegramMessages.received_at.desc(),
                            TelegramMessages.message_id.desc(),
                        )
                        .limit(3)
                    )
                )
            )
        )

    if not isinstance(chat_id, int):
        return
    if off_topic_mode:
        logger.info(
            "send_session_ack_noop_off_topic_mode task_id=%s session_id=%s chat_id=%s",
            task_id,
            session_id,
            chat_id,
        )
        with worker_db_session() as db:
            db_session = db.execute(
                select(TelegramSessions)
                .where(TelegramSessions.id == session_uuid)
                .with_for_update()
            ).scalar_one_or_none()
            if db_session is None or db_session.ack_sent_at is not None:
                return
            db_session.ack_sent_at = dt.datetime.now(dt.UTC)
            db.commit()
        return

    if not should_attempt_backchannel(messages=recent_messages):
        logger.info(
            "send_session_ack_noop_should_not_attempt task_id=%s session_id=%s chat_id=%s",
            task_id,
            session_id,
            chat_id,
        )
        with worker_db_session() as db:
            db_session = db.execute(
                select(TelegramSessions)
                .where(TelegramSessions.id == session_uuid)
                .with_for_update()
            ).scalar_one_or_none()
            if db_session is None or db_session.ack_sent_at is not None:
                return
            db_session.ack_sent_at = dt.datetime.now(dt.UTC)
            db.commit()
        return

    settings = get_settings()
    try:
        ack_text, data, headers, latency_ms = generate_backchannel_text(
            messages=recent_messages,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "send_session_ack_generation_failed",
            extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
        )
        return

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model = data.get("model") if isinstance(data.get("model"), str) else settings.openai_model
    upstream_id = data.get("id") if isinstance(data.get("id"), str) else None
    provider_name = data.get("provider") if isinstance(data.get("provider"), str) else None
    total_cost_usd = None
    usage_obj = data.get("usage") if isinstance(data.get("usage"), dict) else None
    if isinstance(usage_obj, dict) and isinstance(usage_obj.get("cost"), (int, float)):
        total_cost_usd = float(usage_obj["cost"])

    if ack_text is None:
        with worker_db_session() as db:
            db_session = db.execute(
                select(TelegramSessions)
                .where(TelegramSessions.id == session_uuid)
                .with_for_update()
            ).scalar_one_or_none()
            if db_session is None or db_session.ack_sent_at is not None:
                return
            llm_call_id = record_llm_call(
                db=db,
                session_id=session_uuid,
                chat_id=chat_id,
                purpose="ack",
                model=model,
                openrouter_generation_id=generation_id,
                upstream_id=upstream_id,
                provider_name=provider_name,
                usage=usage,
                latency_ms=latency_ms,
                total_cost_usd=total_cost_usd,
                error=None,
            )
            db_session.ack_sent_at = dt.datetime.now(dt.UTC)
            db.commit()

        if generation_id is not None:
            try:
                schedule_openrouter_cost_backfill(llm_call_id=llm_call_id, delay_seconds=120)
            except Exception:
                logger.exception(
                    "send_session_ack_cost_backfill_schedule_failed",
                    extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
                )
        return

    try:
        telegram_message_id = send_message(chat_id=chat_id, text=ack_text, settings=settings)
    except Exception as exc:
        logger.exception(
            "telegram_ack_send_failed",
            extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
        )
        raise

    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None or db_session.ack_sent_at is not None:
            return
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_uuid,
            chat_id=chat_id,
            purpose="ack",
            model=model,
            openrouter_generation_id=generation_id,
            upstream_id=upstream_id,
            provider_name=provider_name,
            usage=usage,
            latency_ms=latency_ms,
            total_cost_usd=total_cost_usd,
            error=None,
        )
        record_outgoing_message(
            db=db,
            session_id=session_uuid,
            chat_id=chat_id,
            kind="ack",
            text=ack_text,
            telegram_message_id=telegram_message_id,
            llm_call_id=llm_call_id,
        )
        db_session.ack_sent_at = dt.datetime.now(dt.UTC)
        db.commit()

    if generation_id is not None:
        try:
            schedule_openrouter_cost_backfill(llm_call_id=llm_call_id, delay_seconds=120)
        except Exception:
            logger.exception(
                "send_session_ack_cost_backfill_schedule_failed",
                extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
            )


@celery_app.task(name="send_message_backchannel")
def send_message_backchannel(*, session_id: str) -> None:
    """
    Per-message backchannel: runs as soon as a message is ingested (best-effort).

    Unlike send_session_ack, this does NOT touch TelegramSessions.ack_sent_at, so it
    won't suppress the session-level ack (unless that ack sees prior backchannels).
    """
    task_id = _get_task_id()
    session_uuid = _parse_uuid(session_id)
    logger.info(
        "celery_task_started name=send_message_backchannel task_id=%s session_id=%s",
        task_id,
        session_id,
    )

    chat_id: int | None = None
    off_topic_mode = False
    recent_messages: list[TelegramMessages] = []
    with worker_db_session() as db:
        db_session = db.scalar(select(TelegramSessions).where(TelegramSessions.id == session_uuid))
        if db_session is None:
            return
        if db_session.status != "open":
            return
        chat_id = db_session.chat_id
        state = db.scalar(select(TelegramChatStates).where(TelegramChatStates.chat_id == chat_id))
        if state is not None:
            off_topic_mode = bool(state.off_topic_mode)
        recent_messages = list(
            reversed(
                list(
                    db.scalars(
                        select(TelegramMessages)
                        .where(TelegramMessages.session_id == session_uuid)
                        .order_by(
                            TelegramMessages.received_at.desc(),
                            TelegramMessages.message_id.desc(),
                        )
                        .limit(5)
                    )
                )
            )
        )

    if not isinstance(chat_id, int):
        return
    if off_topic_mode:
        return
    if not should_attempt_backchannel(messages=recent_messages, skip_probability=0.0):
        return

    settings = get_settings()
    try:
        window = min(len(recent_messages), random.randint(2, 5))
        ack_text, data, headers, latency_ms = generate_backchannel_text(
            messages=recent_messages[-window:],
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "send_message_backchannel_generation_failed",
            extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
        )
        return

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model = data.get("model") if isinstance(data.get("model"), str) else settings.openai_model
    upstream_id = data.get("id") if isinstance(data.get("id"), str) else None
    provider_name = data.get("provider") if isinstance(data.get("provider"), str) else None
    total_cost_usd = None
    usage_obj = data.get("usage") if isinstance(data.get("usage"), dict) else None
    if isinstance(usage_obj, dict) and isinstance(usage_obj.get("cost"), (int, float)):
        total_cost_usd = float(usage_obj["cost"])

    if ack_text is None:
        with worker_db_session() as db:
            llm_call_id = record_llm_call(
                db=db,
                session_id=session_uuid,
                chat_id=chat_id,
                purpose="ack_message",
                model=model,
                openrouter_generation_id=generation_id,
                upstream_id=upstream_id,
                provider_name=provider_name,
                usage=usage,
                latency_ms=latency_ms,
                total_cost_usd=total_cost_usd,
                error=None,
            )
            db.commit()
        if generation_id is not None:
            try:
                schedule_openrouter_cost_backfill(llm_call_id=llm_call_id, delay_seconds=120)
            except Exception:
                logger.exception(
                    "send_message_backchannel_cost_backfill_schedule_failed",
                    extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
                )
        return

    try:
        telegram_message_id = send_message(chat_id=chat_id, text=ack_text, settings=settings)
    except Exception as exc:
        logger.exception(
            "telegram_message_backchannel_send_failed",
            extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
        )
        return

    with worker_db_session() as db:
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_uuid,
            chat_id=chat_id,
            purpose="ack_message",
            model=model,
            openrouter_generation_id=generation_id,
            upstream_id=upstream_id,
            provider_name=provider_name,
            usage=usage,
            latency_ms=latency_ms,
            total_cost_usd=total_cost_usd,
            error=None,
        )
        record_outgoing_message(
            db=db,
            session_id=session_uuid,
            chat_id=chat_id,
            kind=MESSAGE_BACKCHANNEL_KIND,
            text=ack_text,
            telegram_message_id=telegram_message_id,
            llm_call_id=llm_call_id,
        )
        db.commit()

    if generation_id is not None:
        try:
            schedule_openrouter_cost_backfill(llm_call_id=llm_call_id, delay_seconds=120)
        except Exception:
            logger.exception(
                "send_message_backchannel_cost_backfill_schedule_failed",
                extra={"llm_call_id": str(llm_call_id), "session_id": session_id},
            )


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
        stale_open_sessions = db.execute(
            select(TelegramSessions)
            .where(
                TelegramSessions.status == "open",
                TelegramSessions.started_at < stale_threshold,
            )
            .with_for_update()
        ).scalars().all()

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
                            {"message_count": message_count, "age_minutes": (now - _coerce_utc(session.started_at)).total_seconds() / 60},
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )
                db.commit()
                # Enqueue processing
                cast(CeleryDelayable, process_session).delay(session_id=str(session.id))
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
                            {"age_minutes": (now - _coerce_utc(session.started_at)).total_seconds() / 60},
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
        stuck_processing_sessions = db.execute(
            select(TelegramSessions)
            .where(
                TelegramSessions.status == "processing",
                TelegramSessions.started_at < stuck_processing_threshold,
            )
            .with_for_update()
        ).scalars().all()

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
                        {"age_hours": (now - _coerce_utc(session.started_at)).total_seconds() / 3600},
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
        # The periodic close_stale_sessions task will also catch these, but
        # we close immediately to avoid leaving sessions open
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
    return
