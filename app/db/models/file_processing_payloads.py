from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileProcessingPayloads(Base):
    __tablename__: ClassVar[str] = "file_processing_payloads"  # type: ignore[override]

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("file_processing_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
