from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_VALID_STATUSES = ("draft", "sent", "received", "cancelled")


class PurchaseOrder(Base):
    """Purchase order sent (or to be sent) to a supplier."""

    __tablename__: ClassVar[str] = "purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sent', 'received', 'cancelled')",
            name="ck_purchase_orders_status",
        ),
        Index("ix_purchase_orders_restaurant_status", "restaurant_id", "status"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot of supplier name at creation time (persists even if supplier is deleted)
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
