from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import random
import uuid
from typing import cast

from celery import current_task
from sqlalchemy import func, select

from app.ai.openai_client import OpenAIError
from app.ai.backchannel import generate_backchannel_text, should_attempt_backchannel
from app.ai.openrouter_generation import fetch_openrouter_generation
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import get_settings
from app.db.models.llm_calls import LLMCalls
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_chat_states import TelegramChatStates
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.telegram_session import TelegramSessions
from app.processing.session_processor import process_session as process_session_impl
from app.telegram.handler import handle_update
from app.telegram.bot_api import send_message
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryApplyAsync, CeleryDelayable
from app.workers.db import worker_db_session
from app.workers.telemetry import (
    record_llm_call,
    record_outgoing_message,
    schedule_openrouter_cost_backfill,
)

logger = logging.getLogger(__name__)

SESSION_FLUSH_REPLY_TEXT = "Got it — I'm on it."
MESSAGE_BACKCHANNEL_KIND = "ack_message"


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
    ack_countdown = 1.0 + random.random() * 2.0
    cast(CeleryApplyAsync, send_session_ack).apply_async(
        kwargs={"session_id": session_id}, countdown=ack_countdown
    )
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

    logger.info(
        "process_session_completed task_id=%s session_id=%s",
        task_id,
        session_id,
    )
    return


@celery_app.task(name="backfill_llm_call_costs")
def backfill_llm_call_costs(*, llm_call_id: str) -> None:
    task_id = _get_task_id()
    llm_call_uuid = _parse_uuid(llm_call_id)

    logger.info(
        "celery_task_started name=backfill_llm_call_costs task_id=%s llm_call_id=%s",
        task_id,
        llm_call_id,
    )

    with worker_db_session() as db:
        row = db.execute(
            select(LLMCalls).where(LLMCalls.id == llm_call_uuid).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            logger.info(
                "backfill_llm_call_costs_noop_not_found task_id=%s llm_call_id=%s",
                task_id,
                llm_call_id,
            )
            return
        if row.cost_backfilled_at is not None:
            logger.info(
                "backfill_llm_call_costs_noop_already_backfilled task_id=%s llm_call_id=%s",
                task_id,
                llm_call_id,
            )
            return
        generation_id = row.openrouter_generation_id
        if not isinstance(generation_id, str) or not generation_id.strip():
            logger.info(
                "backfill_llm_call_costs_noop_missing_generation_id task_id=%s llm_call_id=%s",
                task_id,
                llm_call_id,
            )
            return

    settings = get_settings()
    payload = fetch_openrouter_generation(settings=settings, generation_id=generation_id)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise OpenAIError(f"Unexpected OpenRouter generation payload shape: {payload!r}")

    total_cost = data.get("total_cost")
    cache_discount = data.get("cache_discount")
    upstream_inference_cost = data.get("upstream_inference_cost")

    def _to_decimal(value: object) -> decimal.Decimal | None:
        if value is None:
            return None
        if isinstance(value, (int, float, decimal.Decimal)):
            return decimal.Decimal(str(value))
        return None

    total_cost_dec = _to_decimal(total_cost)
    cache_discount_dec = _to_decimal(cache_discount)
    upstream_inference_cost_dec = _to_decimal(upstream_inference_cost)

    provider_name = data.get("provider_name")
    upstream_id = data.get("upstream_id")

    now = dt.datetime.now(dt.UTC)
    with worker_db_session() as db:
        row = db.execute(
            select(LLMCalls).where(LLMCalls.id == llm_call_uuid).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return
        row.total_cost_usd = total_cost_dec
        row.cache_discount_usd = cache_discount_dec
        row.upstream_inference_cost_usd = upstream_inference_cost_dec
        if isinstance(provider_name, str) and provider_name.strip():
            row.provider_name = provider_name.strip()
        if isinstance(upstream_id, str) and upstream_id.strip():
            row.upstream_id = upstream_id.strip()
        row.openrouter_generation_json = payload
        row.cost_backfilled_at = now
        db.commit()

    logger.info(
        "backfill_llm_call_costs_completed task_id=%s llm_call_id=%s generation_id=%s total_cost=%s",
        task_id,
        llm_call_id,
        generation_id,
        str(total_cost_dec) if total_cost_dec is not None else None,
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
