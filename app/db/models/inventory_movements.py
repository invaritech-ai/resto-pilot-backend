from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryMovements(Base):
    __tablename__: ClassVar[str] = "inventory_movements"  # type: ignore[override]

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    inventory_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_batches.id", ondelete="CASCADE"), nullable=False
    )
    movement_type: Mapped[str] = mapped_column(
        Enum("receive", "consume", "waste", "adjust", name="movement_type"),
        nullable=False,
    )
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
