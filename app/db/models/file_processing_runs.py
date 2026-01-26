from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileProcessingRuns(Base):
    __tablename__: ClassVar[str] = "file_processing_runs"  # type: ignore[override]

    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    file_id: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str | None] = mapped_column(
        Enum("api", "telegram", name="file_processing_source"), nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telegram_sessions.id", ondelete="SET NULL"), nullable=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )

    processing_type: Mapped[str] = mapped_column(
        Enum("invoice", "price_list", "inventory", name="processing_type"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("processing", "completed", "failed", "cancelled", name="file_processing_run_status"),
        nullable=False,
        server_default="processing",
    )
    pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    current_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
