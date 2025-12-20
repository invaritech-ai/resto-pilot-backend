from __future__ import annotations

import datetime as dt
from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from typing import ClassVar


class User(Base):
    __tablename__: ClassVar[str] = "users"  # type: ignore[override]
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    is_phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    state: Mapped[str | None] = mapped_column(
        String(50), nullable=True, server_default="IDLE"
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_interaction_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
