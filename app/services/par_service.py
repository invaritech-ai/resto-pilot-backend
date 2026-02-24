"""
Par level service — minimum stock threshold management.

Responsibilities:
    set_par()              — upsert par level for an item (idempotent)
    get_par_level()        — single lookup by restaurant + item
    list_par_levels()      — paginated par levels with current balances
    count_par_levels()     — total count for pagination
    get_below_par_items()  — items where balance < par_qty, with best price hint

Invariants:
    - One par level row per (restaurant_id, inventory_item_id).
    - Upsert via ON CONFLICT DO UPDATE to avoid races.
    - Quantities stay in NUMERIC(12,3) display space — not integer minor units.
    - Best price lookup is approximate: fuzzy match on item_name_lower, threshold 0.5.
      Returns cheapest price across all linked suppliers for the restaurant.

Callers own the commit. This service only flushes within the caller's transaction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.inventory_balances import InventoryBalance
from app.db.models.inventory_items import InventoryItem
from app.db.models.inventory_par_levels import InventoryParLevel
from app.db.models.restaurant_suppliers import RestaurantSupplier
from app.db.models.supplier_price_lists import SupplierPriceList
from app.db.models.supplier_prices import SupplierPrice
from app.db.models.suppliers import Supplier

logger = logging.getLogger(__name__)


class ParLevelNotFoundError(Exception):
    pass


@dataclass
class BelowParItem:
    """One item that is below its par level."""

    item: InventoryItem
    balance: Decimal        # current qty (0 if no balance row)
    par_qty: Decimal        # minimum threshold
    gap: Decimal            # par_qty - balance (always > 0)
    unit: str               # from par level record
    best_price_minor: int | None        # unit_price_minor from cheapest supplier
    best_price_exp: int | None          # exponent for display
    best_price_currency: str | None
    best_supplier_name: str | None
    best_supplier_id: uuid.UUID | None  # for reorder keyboard callback


class ParService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set_par(
        self,
        restaurant_id: uuid.UUID,
        inventory_item_id: uuid.UUID,
        par_qty: Decimal,
        unit: str,
        user_id: uuid.UUID,
    ) -> tuple[InventoryParLevel, bool]:
        """Upsert par level for an item. Returns (par_level, created).

        created=True  → new row inserted.
        created=False → existing row updated.

        Raises:
            ValueError: par_qty ≤ 0 or inventory_item_id not in restaurant.
        """
        if par_qty <= 0:
            raise ValueError(f"par_qty must be positive, got {par_qty}")

        # Ownership guard
        item = self.session.scalar(
            select(InventoryItem).where(
                InventoryItem.id == inventory_item_id,
                InventoryItem.restaurant_id == restaurant_id,
            )
        )
        if item is None:
            raise ValueError(
                f"inventory_item_id {inventory_item_id} does not belong to "
                f"restaurant {restaurant_id}"
            )

        existing = self.session.scalar(
            select(InventoryParLevel).where(
                InventoryParLevel.restaurant_id == restaurant_id,
                InventoryParLevel.inventory_item_id == inventory_item_id,
            )
        )

        stmt = (
            pg_insert(InventoryParLevel)
            .values(
                restaurant_id=restaurant_id,
                inventory_item_id=inventory_item_id,
                par_qty=float(par_qty),
                unit=unit.strip(),
                created_by=user_id,
            )
            .on_conflict_do_update(
                constraint="uq_inventory_par_levels_restaurant_item",
                set_={
                    "par_qty": float(par_qty),
                    "unit": unit.strip(),
                    "updated_at": func.now(),
                },
            )
            .returning(InventoryParLevel)
        )
        result = self.session.execute(stmt)
        par_level = result.scalar_one()
        return par_level, existing is None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_par_level(
        self,
        restaurant_id: uuid.UUID,
        inventory_item_id: uuid.UUID,
    ) -> InventoryParLevel | None:
        """Return par level for a specific item, or None if not set."""
        return self.session.scalar(
            select(InventoryParLevel).where(
                InventoryParLevel.restaurant_id == restaurant_id,
                InventoryParLevel.inventory_item_id == inventory_item_id,
            )
        )

    def list_par_levels(
        self,
        restaurant_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[tuple[InventoryParLevel, InventoryItem, InventoryBalance | None]]:
        """Paginated (ParLevel, InventoryItem, InventoryBalance|None), ordered by item name.

        Balance is None for items with no transactions yet.
        """
        stmt = (
            select(InventoryParLevel, InventoryItem, InventoryBalance)
            .join(InventoryItem, InventoryItem.id == InventoryParLevel.inventory_item_id)
            .outerjoin(
                InventoryBalance,
                (InventoryBalance.item_id == InventoryParLevel.inventory_item_id)
                & (InventoryBalance.restaurant_id == restaurant_id),
            )
            .where(InventoryParLevel.restaurant_id == restaurant_id)
            .order_by(InventoryItem.name_lower)
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], row[1], row[2]) for row in rows]

    def count_par_levels(self, restaurant_id: uuid.UUID) -> int:
        """Total par level count for a restaurant."""
        stmt = select(func.count()).where(
            InventoryParLevel.restaurant_id == restaurant_id
        )
        return self.session.scalar(stmt) or 0

    def get_below_par_items(self, restaurant_id: uuid.UUID) -> list[BelowParItem]:
        """Return items whose current balance is below their par level.

        Sorted by gap descending (most urgent first).
        For each item, attempts to find the cheapest unit price from any
        linked supplier's price list (approximate fuzzy match on item name,
        threshold 0.5). Returns None price fields if no price data exists.

        This runs 1 query to get below-par items, then 1 price-lookup query
        per item. Designed for short lists (typically ≤10 items).
        """
        # Step 1: get all par levels for this restaurant with balances
        stmt = (
            select(InventoryParLevel, InventoryItem, InventoryBalance)
            .join(InventoryItem, InventoryItem.id == InventoryParLevel.inventory_item_id)
            .outerjoin(
                InventoryBalance,
                (InventoryBalance.item_id == InventoryParLevel.inventory_item_id)
                & (InventoryBalance.restaurant_id == restaurant_id),
            )
            .where(InventoryParLevel.restaurant_id == restaurant_id)
            .order_by(InventoryItem.name_lower)
        )
        rows = self.session.execute(stmt).all()

        results: list[BelowParItem] = []
        for par, item, balance in rows:
            current = Decimal(str(balance.balance)) if balance else Decimal("0")
            par_qty = Decimal(str(par.par_qty))
            if current >= par_qty:
                continue  # at or above par — skip

            gap = par_qty - current

            # Step 2: find cheapest price for this item from linked suppliers
            best_price_minor: int | None = None
            best_price_exp: int | None = None
            best_price_currency: str | None = None
            best_supplier_name: str | None = None

            best_supplier_id: uuid.UUID | None = None

            price_row = self._find_best_price(restaurant_id, item.name_lower)
            if price_row:
                sp, sup_name, sup_id = price_row
                best_price_minor = sp.price_minor
                best_price_exp = sp.price_exp
                best_price_currency = sp.currency
                best_supplier_name = sup_name
                best_supplier_id = sup_id

            results.append(
                BelowParItem(
                    item=item,
                    balance=current,
                    par_qty=par_qty,
                    gap=gap,
                    unit=par.unit,
                    best_price_minor=best_price_minor,
                    best_price_exp=best_price_exp,
                    best_price_currency=best_price_currency,
                    best_supplier_name=best_supplier_name,
                    best_supplier_id=best_supplier_id,
                )
            )

        # Sort by gap descending (most urgent first)
        results.sort(key=lambda x: x.gap, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_best_price(
        self,
        restaurant_id: uuid.UUID,
        item_name_lower: str,
    ) -> tuple[SupplierPrice, str, uuid.UUID] | None:
        """Find the cheapest unit price for an item from linked suppliers.

        Uses fuzzy similarity match on item_name_lower (threshold=0.5).
        Returns (SupplierPrice, supplier_name, supplier_id) or None if no price data found.
        The "cheapest" price is determined by price_minor/10^price_exp.
        Items with no price_minor are ignored.
        """
        threshold = 0.5
        score_expr = func.similarity(SupplierPrice.item_name_lower, item_name_lower)

        stmt = (
            select(SupplierPrice, Supplier.name, Supplier.id)
            .join(SupplierPriceList, SupplierPrice.price_list_id == SupplierPriceList.id)
            .join(Supplier, SupplierPrice.supplier_id == Supplier.id)
            .join(RestaurantSupplier, RestaurantSupplier.supplier_id == Supplier.id)
            .where(
                SupplierPriceList.restaurant_id == restaurant_id,
                RestaurantSupplier.restaurant_id == restaurant_id,
                RestaurantSupplier.is_active == True,  # noqa: E712
                SupplierPrice.price_minor.isnot(None),
                score_expr >= threshold,
            )
            .order_by(score_expr.desc())
            .limit(10)
        )
        rows = self.session.execute(stmt).all()
        if not rows:
            return None

        # Pick cheapest by normalised unit price
        best = None
        best_normalised: float = float("inf")
        for sp, sup_name, sup_id in rows:
            if sp.price_minor is None or sp.price_exp is None:
                continue
            normalised = sp.price_minor / (10 ** sp.price_exp)
            if normalised < best_normalised:
                best_normalised = normalised
                best = (sp, sup_name, sup_id)

        return best
