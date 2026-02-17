from __future__ import annotations

import datetime as dt
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, Enum, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramSessions(Base):
    __tablename__: ClassVar[str] = "telegram_sessions"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_sessions_chat_id_status", "chat_id", "status"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_activity_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum("open", "processing", "closed", name="telegram_session_status"),
        nullable=False,
    )

    closed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ack_sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
