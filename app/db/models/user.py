from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.restaurant import Restaurant


class User(Base):
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    is_phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    role: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="owner"
    )
    state: Mapped[str | None] = mapped_column(
        String(50), nullable=True, server_default="IDLE"
    )

    current_restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_interaction_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owned_restaurants: Mapped[list["Restaurant"]] = relationship(
        back_populates="owner",
        foreign_keys="Restaurant.owner_user_id",
    )
    current_restaurant: Mapped["Restaurant | None"] = relationship(
        back_populates="active_users",
        foreign_keys=[current_restaurant_id],
        passive_deletes=True,
    )
