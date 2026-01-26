from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Suppliers(Base):
    __tablename__: ClassVar[str] = "suppliers"  # type: ignore[override]

    # Supplier ownership: suppliers are user-scoped. Restaurants link to suppliers via restaurant_suppliers.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_normalized: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def normalize_supplier_name(name: str | None) -> str:
    """Normalize supplier names for de-duplication."""
    if not name:
        return ""
    normalized = re.sub(r"\s+", " ", name.strip().lower())
    return normalized
