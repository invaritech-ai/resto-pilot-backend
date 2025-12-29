from __future__ import annotations

import datetime as dt
from sqlalchemy import BigInteger, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from typing import ClassVar


class TelegramSessions(Base):
    __tablename__: ClassVar[str] = "telegram_sessions"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_sessions_chat_id_status", "chat_id", "status"),
        Index("ix_telegram_sessions_status_flush_at", "status", "flush_at"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_activity_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    flush_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum("open", "processing", "closed", name="telegram_session_status"),
        nullable=False,
    )

    hint_command: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ack_sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
