from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryBalance(Base):
    """Running balance — updated atomically with every transaction. O(1) reads."""

    __tablename__: ClassVar[str] = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "item_id", name="uq_inventory_balances_restaurant_item"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=False
    )
    balance: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False, server_default="0"
    )
    last_txn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
