from __future__ import annotations

import datetime as dt
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Supplier(Base):
    __tablename__: ClassVar[str] = "suppliers"
    __table_args__ = (
        Index("idx_suppliers_name_lower", "name_lower"),
        # GIN trigram index created manually in migration (requires pg_trgm)
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    name_lower: Mapped[str] = mapped_column(String, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    default_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
