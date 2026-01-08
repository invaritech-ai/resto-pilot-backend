from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SupplierItems(Base):
    __tablename__: ClassVar[str] = "supplier_items"  # type: ignore[override]

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    supplier_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    supplier_name_raw: Mapped[str] = mapped_column(String, nullable=False)
    pack_size_text: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_basis: Mapped[str | None] = mapped_column(
        Enum("kg", "pack", "piece", name="unit_type"), nullable=True
    )
    min_order_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "discontinued", name="supplier_item_status"), nullable=False
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

