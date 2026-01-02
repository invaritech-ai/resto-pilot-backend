from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any, Mapping, cast

from sqlalchemy.orm import Session

from app.db.models.llm_calls import LLMCalls
from app.workers.celery_types import CeleryApplyAsync


def record_llm_call(
    *,
    db: Session,
    session_id: uuid.UUID,
    chat_id: int | None,
    purpose: str,
    model: str,
    openrouter_generation_id: str | None = None,
    upstream_id: str | None = None,
    provider_name: str | None = None,
    usage: Mapping[str, int] | None = None,
    latency_ms: int | None = None,
    total_cost_usd: float | None = None,
    cost_backfilled_at: dt.datetime | None = None,
    openrouter_generation_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> uuid.UUID:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    if usage is not None:
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        prompt_tokens = pt if isinstance(pt, int) else None
        completion_tokens = ct if isinstance(ct, int) else None
        total_tokens = tt if isinstance(tt, int) else None

    total_cost_value = (
        decimal.Decimal(str(total_cost_usd)) if total_cost_usd is not None else None
    )

    row = LLMCalls(
        session_id=session_id,
        chat_id=chat_id,
        purpose=purpose,
        model=model,
        openrouter_generation_id=openrouter_generation_id,
        upstream_id=upstream_id,
        provider_name=provider_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        total_cost_usd=total_cost_value,
        cost_backfilled_at=cost_backfilled_at,
        openrouter_generation_json=openrouter_generation_json,
        error=error,
    )
    db.add(row)
    db.flush()
    return row.id


def schedule_openrouter_cost_backfill(*, llm_call_id: uuid.UUID, delay_seconds: int = 120) -> str | None:
    from app.workers.tasks import backfill_llm_call_costs  # imported lazily

    async_result = cast(CeleryApplyAsync, backfill_llm_call_costs).apply_async(
        kwargs={"llm_call_id": str(llm_call_id)},
        countdown=float(max(0, int(delay_seconds))),
    )
    return getattr(async_result, "id", None)
