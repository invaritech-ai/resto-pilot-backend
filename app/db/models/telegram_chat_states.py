from __future__ import annotations

import datetime as dt
from typing import ClassVar

from sqlalchemy import BigInteger, Boolean, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramChatStates(Base):
    __tablename__: ClassVar[str] = "telegram_chat_states"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_chat_states_off_topic_mode", "off_topic_mode"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    off_topic_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    off_topic_since: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

