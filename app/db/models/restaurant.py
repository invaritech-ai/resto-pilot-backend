from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.user import User

from app.db.base import Base


class Restaurant(Base):
    name: Mapped[str] = mapped_column(String, nullable=False)
    restaurant_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    onboarding_status: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped["User"] = relationship(
        back_populates="owned_restaurants", foreign_keys=[owner_user_id]
    )
    active_users: Mapped[list["User"]] = relationship(
        back_populates="current_restaurant", foreign_keys="User.current_restaurant_id"
    )
