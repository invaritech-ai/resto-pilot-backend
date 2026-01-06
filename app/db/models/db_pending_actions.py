from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _default_expiry() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15)


class DBPendingActions(Base):
    __tablename__: ClassVar[str] = "db_pending_actions"  # type: ignore[override]

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("telegram_sessions.id"), nullable=True
    )

    action_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default="pending"
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_default_expiry
    )
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
