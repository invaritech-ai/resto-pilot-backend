from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Documents(Base):
    __tablename__: ClassVar[str] = "documents"  # type: ignore[override]

    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    doc_type: Mapped[str] = mapped_column(
        Enum("catalogue", "price_list", "promo", "invoice", name="doc_type"),
        nullable=False,
    )
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_date: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    file_url: Mapped[str] = mapped_column(String, nullable=False)  # Telegram file_id
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

