from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductAliases(Base):
    __tablename__: ClassVar[str] = "product_aliases"  # type: ignore[override]

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True
    )
    alias_text: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(
        Enum("auto", "manual", name="alias_confidence"), nullable=False
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

