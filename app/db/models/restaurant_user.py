from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING, ClassVar

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.restaurant import Restaurant
    from app.db.models.user import User


class RestaurantUser(Base):
    __tablename__: ClassVar[str] = "restaurant_users"  # type: ignore[override]
    __table_args__ = (
        UniqueConstraint("restaurant_id", "user_id", name="uq_restaurant_users_member"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Enum("owner", "manager", "staff", name="restaurant_user_role"),
        nullable=False,
        server_default="staff",
    )
    joined_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum("invited", "active", "removed", name="restaurant_user_status"),
        nullable=False,
        server_default="invited",
    )

    restaurant: Mapped["Restaurant"] = relationship(
        back_populates="memberships",
        foreign_keys=[restaurant_id],
    )
    user: Mapped["User"] = relationship(
        back_populates="memberships",
        foreign_keys=[user_id],
    )
    invited_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[invited_by],
    )
