from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryBatches(Base):
    __tablename__: ClassVar[str] = "inventory_batches"  # type: ignore[override]

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    invoice_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice_line_items.id", ondelete="SET NULL"), nullable=True
    )
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric, nullable=False)
    received_date: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expiry_date: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_locations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Enum("available", "consumed", "expired", name="batch_status"), nullable=False
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

