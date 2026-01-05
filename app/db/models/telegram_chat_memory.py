from __future__ import annotations

import datetime as dt
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramChatMemory(Base):
    __tablename__: ClassVar[str] = "telegram_chat_memory"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_chat_memory_chat_id", "chat_id"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

