from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import uuid
from typing import cast

from celery import current_task
from sqlalchemy import func, select

from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import fetch_openrouter_generation
from app.core.config import get_settings
from app.db.models.llm_calls import LLMCalls
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_session import TelegramSessions
from app.processing.session_processor import process_session as process_session_impl
from app.telegram.handler import handle_update
from app.telegram.bot_api import send_message
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryApplyAsync, CeleryDelayable
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
    cast(CeleryDelayable, send_session_ack).delay(session_id=session_id)
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
        settings = get_settings()
        try:
            send_message(chat_id=chat_id, text=text, settings=settings)
        except Exception as exc:
            db.rollback()
            logger.exception(
                "telegram_ack_send_failed",
                extra={"error": repr(exc), "chat_id": chat_id, "session_id": session_id},
            )
            raise

        db_session.ack_sent_at = dt.datetime.now(dt.UTC)
        db.commit()

    if not isinstance(chat_id, int):
        return


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
