"""
Inventory service — ledger + balance management.

Responsibilities:
    get_or_create_item()       — idempotent item lookup/creation by name
    fuzzy_match_item()         — GIN trigram search on inventory_items.name_lower
    record_transaction()       — atomic ledger insert + balance upsert
    list_items()               — paginated items with current balances
    count_items()              — total item count for pagination
    get_item_detail()          — single item + last transaction
    get_balance_summary()      — total items, zero-stock count, negative count

Invariants:
    - inventory_transactions is append-only. No UPDATE or DELETE.
    - inventory_balances is always updated in the same transaction as the ledger row.
    - Negative balances are permitted (returns, corrections). Not blocked, flagged in summary.
    - Quantities are NUMERIC(12,3) in display (decimal) space — not integer minor units.

Callers own the commit. This service only flushes within the caller's transaction.
record_transaction() flushes (to get txn.id for the balance FK) but does not commit;
the balance update runs atomically via INSERT ... ON CONFLICT DO UPDATE.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.inventory_balances import InventoryBalance
from app.db.models.inventory_items import InventoryItem
from app.db.models.inventory_transactions import InventoryTransaction


class ItemNotFoundError(Exception):
    pass


@dataclass
class BalanceSummary:
    total_items: int
    zero_stock_count: int
    negative_count: int


class InventoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def get_or_create_item(
        self,
        restaurant_id: uuid.UUID,
        name: str,
        unit: str | None = None,
        supplier_id: uuid.UUID | None = None,
    ) -> tuple[InventoryItem, bool]:
        """Return (item, created). Matches by name_lower; creates if absent.

        The unique constraint on (restaurant_id, name_lower) guarantees no duplicates.
        Caller owns the commit.
        """
        name_lower = name.strip().lower()
        item = self.session.scalar(
            select(InventoryItem).where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.name_lower == name_lower,
            )
        )
        if item is not None:
            return item, False

        item = InventoryItem(
            restaurant_id=restaurant_id,
            name=name.strip(),
            name_lower=name_lower,
            unit=unit,
            supplier_id=supplier_id,
        )
        self.session.add(item)
        self.session.flush()
        return item, True

    def fuzzy_match_item(
        self,
        restaurant_id: uuid.UUID,
        name: str,
        threshold: float = 0.5,
    ) -> list[tuple[InventoryItem, float]]:
        """GIN trigram search on inventory_items.name_lower, scoped to restaurant.

        Args:
            restaurant_id: Restrict to this restaurant.
            name:          Search query.
            threshold:     Minimum similarity (0.0–1.0). Default 0.5.

        Returns:
            List of (InventoryItem, score) tuples, highest score first.
        """
        query_lower = name.strip().lower()
        score = func.similarity(InventoryItem.name_lower, query_lower).label("score")
        stmt = (
            select(InventoryItem, score)
            .where(
                InventoryItem.restaurant_id == restaurant_id,
                func.similarity(InventoryItem.name_lower, query_lower) >= threshold,
            )
            .order_by(score.desc())
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in rows]

    # ------------------------------------------------------------------
    # Transaction + balance (atomic pair)
    # ------------------------------------------------------------------

    def record_transaction(
        self,
        restaurant_id: uuid.UUID,
        item_id: uuid.UUID,
        txn_type: str,
        quantity: float,
        created_by: uuid.UUID,
        source: str = "manual",
        unit_price: float | None = None,
        amount: float | None = None,
        staging_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> InventoryTransaction:
        """Insert ledger row + update balance within the caller's transaction.

        Validates that item_id belongs to restaurant_id before writing.
        Uses an atomic upsert (INSERT ... ON CONFLICT DO UPDATE) for the balance
        row so concurrent first-writes cannot race on the unique constraint.
        Flushes to get txn.id for the balance FK, but does NOT commit.
        Caller must commit after all related work is done.

        Args:
            txn_type: 'credit' or 'debit'
            quantity: Always positive. Sign determined by txn_type.
            source:   'invoice' or 'manual'

        Raises:
            ValueError: txn_type or quantity invalid, or item_id does not
                        belong to restaurant_id.

        Returns:
            The newly created InventoryTransaction (not yet committed).
        """
        if txn_type not in ("credit", "debit"):
            raise ValueError(f"txn_type must be 'credit' or 'debit', got {txn_type!r}")
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")

        # Ownership guard: reject cross-tenant writes at the service boundary.
        item = self.session.scalar(
            select(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.restaurant_id == restaurant_id,
            )
        )
        if item is None:
            raise ValueError(
                f"item_id {item_id} does not belong to restaurant {restaurant_id}"
            )

        txn = InventoryTransaction(
            restaurant_id=restaurant_id,
            item_id=item_id,
            txn_type=txn_type,
            quantity=quantity,
            unit_price=unit_price,
            amount=amount,
            source=source,
            staging_id=staging_id,
            notes=notes,
            created_by=created_by,
        )
        self.session.add(txn)
        self.session.flush()  # get txn.id for the balance FK

        delta = float(quantity) if txn_type == "credit" else -float(quantity)
        now = dt.datetime.now(tz=dt.timezone.utc)

        # Atomic upsert: avoids the SELECT-then-INSERT race on the first write
        # for a given (restaurant_id, item_id). ON CONFLICT DO UPDATE acquires
        # a row lock on the conflicting row, so concurrent updates are safe too.
        upsert_stmt = (
            pg_insert(InventoryBalance)
            .values(
                restaurant_id=restaurant_id,
                item_id=item_id,
                balance=delta,
                last_txn_id=txn.id,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_inventory_balances_restaurant_item",
                set_={
                    "balance": InventoryBalance.balance + delta,
                    "last_txn_id": txn.id,
                    "updated_at": now,
                },
            )
        )
        self.session.execute(upsert_stmt)

        return txn

    def confirm_invoice(
        self,
        restaurant_id: uuid.UUID,
        user_id: uuid.UUID,
        staging_id: uuid.UUID,
        line_items: list[dict],
        resolutions: dict[int, uuid.UUID | None],
    ) -> int:
        """Batch-credit inventory from a confirmed invoice. Caller must commit.

        For each line item, looks up its resolved inventory_item_id from
        `resolutions`. Items mapped to None are skipped (user chose to ignore).
        All transactions + balance updates flush within the caller's session;
        a single commit at the end makes everything atomic.

        Args:
            restaurant_id: The restaurant being updated.
            user_id:       The user confirming (recorded as created_by).
            staging_id:    FK reference stored on each transaction.
            line_items:    Extracted invoice lines, each a dict with at minimum
                           {"name": str, "qty": float} and optionally
                           {"unit_price": float, "amount": float}.
            resolutions:   Mapping of 0-based line item index → inventory_item_id
                           (UUID) or None to skip that line. Keyed by index (not
                           name) so duplicate item names resolve independently.

        Returns:
            Number of transactions created.
        """
        created = 0
        for i, item in enumerate(line_items):
            item_id = resolutions.get(i)
            if item_id is None:
                continue  # user chose to skip this line
            qty = item.get("qty")
            if qty is None:
                logger.warning("confirm_invoice: skipping item idx=%d — no qty", i)
                continue
            self.record_transaction(
                restaurant_id=restaurant_id,
                item_id=item_id,
                txn_type="credit",
                quantity=float(qty),
                created_by=user_id,
                source="invoice",
                unit_price=item.get("unit_price"),
                amount=item.get("amount"),
                staging_id=staging_id,
            )
            created += 1
        return created

    # ------------------------------------------------------------------
    # Read queries
    # ------------------------------------------------------------------

    def list_items(
        self,
        restaurant_id: uuid.UUID,
        offset: int = 0,
        limit: int = 10,
    ) -> list[tuple[InventoryItem, InventoryBalance | None]]:
        """Paginated (InventoryItem, InventoryBalance|None), ordered by name_lower.

        Balance is None for items that have never had a transaction (edge case).
        """
        stmt = (
            select(InventoryItem, InventoryBalance)
            .outerjoin(
                InventoryBalance,
                (InventoryBalance.item_id == InventoryItem.id)
                & (InventoryBalance.restaurant_id == restaurant_id),
            )
            .where(InventoryItem.restaurant_id == restaurant_id)
            .order_by(InventoryItem.name_lower)
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], row[1]) for row in rows]

    def count_items(self, restaurant_id: uuid.UUID) -> int:
        """Total inventory item count for a restaurant."""
        stmt = select(func.count()).where(
            InventoryItem.restaurant_id == restaurant_id
        )
        return self.session.scalar(stmt) or 0

    def get_item_detail(
        self, item_id: uuid.UUID
    ) -> tuple[InventoryItem, InventoryBalance | None, InventoryTransaction | None]:
        """Single item + current balance + most recent transaction.

        Raises:
            ItemNotFoundError: item_id does not exist.
        """
        item = self.session.get(InventoryItem, item_id)
        if item is None:
            raise ItemNotFoundError(f"InventoryItem {item_id} not found")

        balance = self.session.scalar(
            select(InventoryBalance).where(
                InventoryBalance.item_id == item_id,
                InventoryBalance.restaurant_id == item.restaurant_id,
            )
        )
        last_txn: InventoryTransaction | None = None
        if balance and balance.last_txn_id:
            last_txn = self.session.get(InventoryTransaction, balance.last_txn_id)

        return item, balance, last_txn

    def get_balance_summary(self, restaurant_id: uuid.UUID) -> BalanceSummary:
        """Summary stats: total items, zero-stock count, negative-balance count."""
        total = self.count_items(restaurant_id)

        zero_stmt = (
            select(func.count())
            .select_from(InventoryBalance)
            .where(
                InventoryBalance.restaurant_id == restaurant_id,
                InventoryBalance.balance == 0,
            )
        )
        zero_count = self.session.scalar(zero_stmt) or 0

        negative_stmt = (
            select(func.count())
            .select_from(InventoryBalance)
            .where(
                InventoryBalance.restaurant_id == restaurant_id,
                InventoryBalance.balance < 0,
            )
        )
        negative_count = self.session.scalar(negative_stmt) or 0

        return BalanceSummary(
            total_items=total,
            zero_stock_count=zero_count,
            negative_count=negative_count,
        )
