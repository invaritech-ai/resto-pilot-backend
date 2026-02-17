from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramOutgoingMessages(Base):
    __tablename__: ClassVar[str] = "telegram_outgoing_messages"  # type: ignore[override]
    __table_args__ = (
        Index("ix_telegram_outgoing_messages_session_id_sent_at", "session_id", "sent_at"),
        Index("ix_telegram_outgoing_messages_chat_id_sent_at", "chat_id", "sent_at"),
        Index("ix_telegram_outgoing_messages_kind_sent_at", "kind", "sent_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("telegram_sessions.id"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    kind: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)

    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("llm_calls.id"), nullable=True
    )

    sent_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

