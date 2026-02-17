from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HandshakeRequest(Base):
    __tablename__: ClassVar[str] = "handshake_requests"
    __table_args__ = (
        Index("idx_handshake_staging", "staging_id", "resolved_at"),
        Index("idx_handshake_user", "user_id", "resolved_at"),
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_processing_staging.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)  # "confirm_supplier" | "confirm_unit" | "confirm_currency"
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
