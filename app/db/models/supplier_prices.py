from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SupplierPrice(Base):
    __tablename__: ClassVar[str] = "supplier_prices"
    __table_args__ = (
        Index("idx_supplier_prices_list", "price_list_id"),
        Index("idx_supplier_prices_supplier", "supplier_id", "item_name_lower"),
        # GIN trigram index on item_name_lower created manually in migration (requires pg_trgm)
    )

    price_list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_price_lists.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    item_name_lower: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_qty_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit_qty_exp: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_exp: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
