from __future__ import annotations

import datetime as dt
from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

import uuid

from app.db.base import Base
from typing import ClassVar


class ProcessingEvents(Base):
    __tablename__: ClassVar[str] = "processing_events"  # type: ignore[override]
    __table_args__ = (Index("ix_processing_events_session_id_at", "session_id", "at"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("telegram_sessions.id"), nullable=False
    )

    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    event: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
