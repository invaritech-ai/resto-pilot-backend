from __future__ import annotations

import datetime as dt
import decimal
import logging

from sqlalchemy import select

from app.ai.openai_client import OpenAIError
from app.ai.openrouter_generation import fetch_openrouter_generation
from app.core.config import get_settings
from app.db.models.llm_calls import LLMCalls
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id, _parse_uuid

logger = logging.getLogger(__name__)


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
