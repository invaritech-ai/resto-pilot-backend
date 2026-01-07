from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PriceComparisons(Base):
    __tablename__: ClassVar[str] = "price_comparisons"  # type: ignore[override]

    invoice_line_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice_line_items.id", ondelete="CASCADE"), nullable=False
    )
    supplier_price_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_prices.id", ondelete="CASCADE"), nullable=False
    )
    expected_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    actual_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    delta: Mapped[float] = mapped_column(Numeric, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("ok", "warning", "dispute", name="comparison_status"), nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

