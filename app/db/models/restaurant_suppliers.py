from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RestaurantSuppliers(Base):
    __tablename__: ClassVar[str] = "restaurant_suppliers"  # type: ignore[override]
    __table_args__ = (
        UniqueConstraint("restaurant_id", "supplier_id", name="uq_restaurant_suppliers_restaurant_id_supplier_id"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "inactive", name="restaurant_supplier_status"),
        nullable=False,
        server_default="active",
    )
    account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    default_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
