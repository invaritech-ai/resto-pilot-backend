from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.restaurant import Restaurant
    from app.db.models.user import User


class InviteCodes(Base):
    __tablename__: ClassVar[str] = "invite_codes"  # type: ignore[override]
    __table_args__ = (
        UniqueConstraint("code", name="uq_invite_codes_code"),
    )

    code: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
    )
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Enum("owner", "manager", "staff", name="invite_code_role"),
        nullable=False,
        server_default="staff",
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    restaurant: Mapped["Restaurant"] = relationship(
        foreign_keys=[restaurant_id],
    )
    created_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[created_by],
    )
