from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileProcessingSteps(Base):
    __tablename__: ClassVar[str] = "file_processing_steps"  # type: ignore[override]

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("started", "completed", "failed", name="file_processing_step_status"),
        nullable=False,
        server_default="started",
    )

    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    request_json: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    response_json: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    usage_json: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
