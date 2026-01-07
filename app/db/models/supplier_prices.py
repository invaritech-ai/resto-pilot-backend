from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SupplierPrices(Base):
    __tablename__: ClassVar[str] = "supplier_prices"  # type: ignore[override]

    supplier_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_items.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    price_type: Mapped[str] = mapped_column(
        Enum("standard", "promo", "special", name="price_type"), nullable=False
    )
    valid_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_to: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    min_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

