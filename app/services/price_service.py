"""
Price service — price list confirmation and supplier catalogue management.

Responsibilities:
    confirm_price_list()    — write supplier_price_list + supplier_prices from staging
    enrich_supplier()       — null-fill supplier contact fields from extracted data

Design decisions:
    - Invoice confirmation does NOT write to supplier_prices (kept separate from purchase history).
    - Price list confirmation writes to supplier_price_lists + supplier_prices only.
    - Supplier contact enrichment is null-fill only: never overwrites existing data.
    - Callers own the commit. This service only flushes.

Price storage:
    SupplierPrice.price_minor + price_exp = integer minor-unit representation.
    E.g. $8.50 SGD → exp=infer_exp("SGD")=2, minor=850.
    Uses app.services.money.infer_exp() + to_minor() for conversion.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.supplier_price_lists import SupplierPriceList
from app.db.models.supplier_prices import SupplierPrice
from app.db.models.suppliers import Supplier
from app.services.money import infer_exp, to_minor as _money_to_minor
from app.services.staging_service import StagingNotFoundError, StagingService

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> dt.date | None:
    """Parse YYYY-MM-DD string to date, return None on failure."""
    if not date_str:
        return None
    try:
        return dt.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


class PriceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def confirm_price_list(
        self,
        staging_id: uuid.UUID,
        restaurant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        """Confirm a price list staging record: write catalogue prices + enrich supplier.

        Creates a SupplierPriceList record and inserts SupplierPrice rows for each
        valid line item. Enriches the Supplier record with any contact info present
        in extracted_data_json (null-fill only — never overwrites existing data).

        Args:
            staging_id:    staging record containing extracted_data_json
            restaurant_id: restaurant this price list belongs to
            user_id:       user performing the confirmation (for logging)

        Returns:
            Number of SupplierPrice rows inserted.

        Raises:
            StagingNotFoundError:  staging record not found.
            ValueError:            staging has no supplier_id set.
        """
        staging = StagingService(self.session).require(staging_id)

        if staging.supplier_id is None:
            raise ValueError(
                f"Staging {staging_id} has no supplier_id — supplier must be resolved before confirming"
            )

        data = staging.extracted_data_json or {}
        supplier_id = staging.supplier_id

        # Load supplier for enrichment
        supplier = self.session.get(Supplier, supplier_id)
        if supplier is None:
            raise ValueError(f"Supplier {supplier_id} not found")

        # --- Create price list record ---
        price_list = SupplierPriceList(
            restaurant_id=restaurant_id,
            supplier_id=supplier_id,
            effective_date=_parse_date(data.get("effective_date")),
        )
        self.session.add(price_list)
        self.session.flush()  # get price_list.id

        # --- Currency resolution ---
        # Prefer extracted currency, fall back to supplier default
        currency = (data.get("currency") or "").strip() or supplier.default_currency

        # --- Insert price rows ---
        count = 0
        for item in data.get("line_items", []):
            name = str(item.get("name") or "").strip()
            unit_price = item.get("unit_price")

            if not name or unit_price is None:
                logger.warning(
                    "price_service: skipping item with missing name or price: %r", item
                )
                continue

            try:
                price_float = float(unit_price)
            except (TypeError, ValueError):
                logger.warning(
                    "price_service: skipping item '%s' — invalid unit_price %r", name, unit_price
                )
                continue

            if price_float <= 0:
                continue

            price_exp = infer_exp(currency)
            price_minor = _money_to_minor(price_float, price_exp)
            unit = str(item.get("unit") or "").strip() or None

            self.session.add(
                SupplierPrice(
                    price_list_id=price_list.id,
                    supplier_id=supplier_id,
                    item_name=name,
                    item_name_lower=name.lower(),
                    unit=unit,
                    price_minor=price_minor,
                    price_exp=price_exp,
                    currency=currency or None,
                )
            )
            count += 1

        # --- Enrich supplier (null-fill only) ---
        self.enrich_supplier(supplier, data)

        logger.info(
            "price_service_confirmed staging_id=%s supplier_id=%s prices=%d",
            staging_id,
            supplier_id,
            count,
        )
        return count

    def enrich_supplier(self, supplier: Supplier, data: dict) -> None:
        """Null-fill supplier contact fields from extracted document data.

        Never overwrites existing non-null values. Caller must flush/commit.

        Fields updated if currently null:
            contact_name, phone, email, default_currency, notes (lead_time appended)
        """
        changed = False

        contact_name = str(data.get("supplier_contact_name") or "").strip() or None
        if contact_name and not supplier.contact_name:
            supplier.contact_name = contact_name
            changed = True

        phone = str(data.get("supplier_phone") or "").strip() or None
        if phone and not supplier.phone:
            supplier.phone = phone
            changed = True

        email = str(data.get("supplier_email") or "").strip() or None
        if email and not supplier.email:
            supplier.email = email
            changed = True

        currency = str(data.get("currency") or "").strip() or None
        if currency and not supplier.default_currency:
            supplier.default_currency = currency
            changed = True

        lead_time = str(data.get("lead_time") or "").strip() or None
        if lead_time and not supplier.notes:
            supplier.notes = f"Lead time: {lead_time}"
            changed = True

        if changed:
            self.session.add(supplier)
            self.session.flush()
            logger.info("price_service_supplier_enriched supplier_id=%s", supplier.id)
