from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryTransaction(Base):
    """Immutable append-only ledger. Never updated after insert."""

    __tablename__: ClassVar[str] = "inventory_transactions"
    __table_args__ = (
        Index("ix_inv_txn_restaurant_item", "restaurant_id", "item_id"),
        Index("ix_inv_txn_staging", "staging_id"),
    )

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=False
    )
    txn_type: Mapped[str] = mapped_column(String, nullable=False)  # 'credit' | 'debit'
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'invoice' | 'manual'
    staging_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("file_processing_staging.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
