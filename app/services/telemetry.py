"""
Telemetry utilities for tracking LLM calls and outgoing messages.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import uuid
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.db.models.llm_calls import LLMCalls
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages

logger = logging.getLogger(__name__)


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
    cache_discount_usd: float | None = None,
    upstream_inference_cost_usd: float | None = None,
    cost_backfilled_at: dt.datetime | None = None,
    openrouter_generation_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> uuid.UUID:
    """Record an LLM call for telemetry.

    For OpenRouter callers pass openrouter_generation_id (response.id starting with 'gen-')
    and total_cost_usd (usage.cost from the response). upstream_inference_cost_usd comes
    from usage.cost_details.upstream_inference_cost. latency_ms should be wall-clock ms
    measured around the API call.
    """
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

    def _to_decimal(v: float | None) -> decimal.Decimal | None:
        return decimal.Decimal(str(v)) if v is not None else None

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
        total_cost_usd=_to_decimal(total_cost_usd),
        cache_discount_usd=_to_decimal(cache_discount_usd),
        upstream_inference_cost_usd=_to_decimal(upstream_inference_cost_usd),
        cost_backfilled_at=cost_backfilled_at,
        openrouter_generation_json=openrouter_generation_json,
        error=error,
    )
    db.add(row)
    db.flush()
    return row.id


def schedule_openrouter_cost_backfill(
    *, llm_call_id: uuid.UUID, delay_seconds: int = 120
) -> str | None:
    """Schedule a task to backfill LLM call costs from OpenRouter."""
    logger.info(
        "openrouter_cost_backfill_skipped llm_call_id=%s delay_seconds=%s",
        llm_call_id,
        delay_seconds,
    )
    return None


def record_outgoing_message(
    *,
    db: Session,
    session_id: uuid.UUID,
    chat_id: int,
    kind: str,
    text: str,
    telegram_message_id: int | None = None,
    llm_call_id: uuid.UUID | None = None,
    sent_at: dt.datetime | None = None,
) -> uuid.UUID:
    """Record an outgoing Telegram message for telemetry."""
    row = TelegramOutgoingMessages(
        session_id=session_id,
        chat_id=chat_id,
        kind=kind,
        text=text,
        telegram_message_id=telegram_message_id,
        llm_call_id=llm_call_id,
        sent_at=sent_at or dt.datetime.now(dt.UTC),
    )
    db.add(row)
    db.flush()
    return row.id
