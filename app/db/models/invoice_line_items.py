from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvoiceLineItems(Base):
    __tablename__: ClassVar[str] = "invoice_line_items"  # type: ignore[override]

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    supplier_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("supplier_items.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    description_raw: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    line_total: Mapped[float] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

