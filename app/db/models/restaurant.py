from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from app.db.models.restaurant_user import RestaurantUser
    from app.db.models.user import User


class Restaurant(Base):
    __tablename__: ClassVar[str] = "restaurants"  # type: ignore[override]

    name: Mapped[str] = mapped_column(String, nullable=False)
    restaurant_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    onboarding_status: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, server_default="{}"
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
    memberships: Mapped[list["RestaurantUser"]] = relationship(
        back_populates="restaurant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
