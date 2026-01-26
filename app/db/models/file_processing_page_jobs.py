from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileProcessingPageJobs(Base):
    __tablename__: ClassVar[str] = "file_processing_page_jobs"  # type: ignore[override]
    __table_args__ = (
        UniqueConstraint("run_id", "page_index", name="uq_file_processing_page_jobs_run_id_page_index"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_processing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "processing",
            "completed",
            "failed_ocr",
            "failed_extraction",
            name="file_processing_page_status",
        ),
        nullable=False,
        server_default="pending",
    )
    ocr_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    extraction_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
