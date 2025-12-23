from __future__ import annotations

import datetime as dt
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

import uuid

from app.db.base import Base
from typing import ClassVar


class TelegramMessages(Base):
    __tablename__: ClassVar[str] = "telegram_messages"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_messages_session_id", "session_id"),
        Index("ix_telegram_messages_chat_id_received_at", "chat_id", "received_at"),
        Index("ix_telegram_messages_file_unique_id", "file_unique_id"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("telegram_sessions.id"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    update_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    text: Mapped[str | None] = mapped_column(String, nullable=True)
    caption: Mapped[str | None] = mapped_column(String, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    file_unique_id: Mapped[str | None] = mapped_column(String, nullable=True)

    file_kind: Mapped[str | None] = mapped_column(String, nullable=True)

    mime: Mapped[str | None] = mapped_column(String, nullable=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
