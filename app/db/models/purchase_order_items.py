from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PurchaseOrderItem(Base):
    """Line item within a purchase order."""

    __tablename__: ClassVar[str] = "purchase_order_items"
    __table_args__ = (
        Index("ix_po_items_po", "po_id"),
        Index("ix_po_items_item", "inventory_item_id"),
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot of item name at order time
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    # Price from supplier_prices at order time; NULL if not known
    unit_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit_price_exp: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Populated when PO is marked received
    received_qty: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
