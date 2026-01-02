from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func, Numeric, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LLMCalls(Base):
    __tablename__: ClassVar[str] = "llm_calls"  # type: ignore[override]
    __table_args__ = (
        Index("ix_llm_calls_session_id_requested_at", "session_id", "requested_at"),
        Index("ix_llm_calls_chat_id_requested_at", "chat_id", "requested_at"),
        Index("ix_llm_calls_purpose_requested_at", "purpose", "requested_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_sessions.id"), nullable=False
    )

    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    purpose: Mapped[str] = mapped_column(String, nullable=False)

    model: Mapped[str] = mapped_column(String, nullable=False)

    openrouter_generation_id: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True
    )
    upstream_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)

    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    total_cost_usd: Mapped[float | None] = mapped_column(Numeric(18, 9), nullable=True)
    cache_discount_usd: Mapped[float | None] = mapped_column(Numeric(18, 9), nullable=True)
    upstream_inference_cost_usd: Mapped[float | None] = mapped_column(
        Numeric(18, 9), nullable=True
    )

    cost_backfilled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    openrouter_generation_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    requested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)

