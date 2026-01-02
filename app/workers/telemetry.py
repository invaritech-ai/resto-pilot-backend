from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any, Mapping, cast

from sqlalchemy.orm import Session

from app.db.models.llm_calls import LLMCalls
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.telegram_chat_states import TelegramChatStates
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


def get_or_create_chat_state(*, db: Session, chat_id: int) -> TelegramChatStates:
    row = db.query(TelegramChatStates).filter(TelegramChatStates.chat_id == chat_id).one_or_none()
    if row is not None:
        return row
    row = TelegramChatStates(chat_id=chat_id, off_topic_mode=False, off_topic_since=None)
    db.add(row)
    db.flush()
    return row


def set_chat_off_topic(*, db: Session, chat_id: int, now: dt.datetime | None = None) -> None:
    row = get_or_create_chat_state(db=db, chat_id=chat_id)
    at = now or dt.datetime.now(dt.UTC)
    row.off_topic_mode = True
    row.off_topic_since = at
    db.add(row)
    db.flush()


def set_chat_on_topic(*, db: Session, chat_id: int) -> None:
    row = get_or_create_chat_state(db=db, chat_id=chat_id)
    row.off_topic_mode = False
    row.off_topic_since = None
    db.add(row)
    db.flush()
