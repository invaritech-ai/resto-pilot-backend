"""
Purchase order service — PO lifecycle management and spend analytics.

Responsibilities:
    create_draft()         — start a new draft PO for a supplier
    add_item()             — add a line item to a draft PO
    remove_item()          — remove a line item from a draft PO
    submit()               — transition draft → sent
    cancel()               — transition draft|sent → cancelled
    mark_received()        — transition sent → received
    list_orders()          — paginated POs, optionally filtered by status
    count_orders()         — total PO count for pagination
    get_order_with_items() — single PO + all its line items
    get_spend_summary()    — aggregate spend on received POs

Status transitions (strictly enforced):
    draft ──submit()──→ sent ──mark_received()──→ received
    draft ──cancel()──→ cancelled
    sent  ──cancel()──→ cancelled

Callers own the commit. This service only flushes within the caller's transaction.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.purchase_order_items import PurchaseOrderItem
from app.db.models.purchase_orders import PurchaseOrder

logger = logging.getLogger(__name__)

_DRAFT = "draft"
_SENT = "sent"
_RECEIVED = "received"
_CANCELLED = "cancelled"


class PONotFoundError(Exception):
    pass


class POStatusError(Exception):
    """Raised when a status transition is not permitted."""
    pass


@dataclass
class SpendRow:
    supplier_name: str
    supplier_id: uuid.UUID | None
    total_display: Decimal
    currency: str
    order_count: int


@dataclass
class SpendSummary:
    period_label: str
    rows: list[SpendRow] = field(default_factory=list)
    grand_total: Decimal = Decimal("0")


class PurchaseOrderService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Create / mutate
    # ------------------------------------------------------------------

    def create_draft(
        self,
        restaurant_id: uuid.UUID,
        supplier_id: uuid.UUID | None,
        supplier_name: str,
        created_by: uuid.UUID,
    ) -> PurchaseOrder:
        """Create a new draft purchase order. Caller must commit.

        supplier_id may be None if the supplier is free-text only.
        supplier_name is stored as a snapshot (won't change if supplier renamed).
        """
        po = PurchaseOrder(
            restaurant_id=restaurant_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name.strip(),
            status=_DRAFT,
            created_by=created_by,
        )
        self.session.add(po)
        self.session.flush()  # populate po.id
        logger.info(
            "purchase_order_service created_draft po_id=%s restaurant_id=%s supplier=%s",
            po.id, restaurant_id, supplier_name,
        )
        return po

    def add_item(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
        inventory_item_id: uuid.UUID | None,
        item_name: str,
        quantity: Decimal,
        unit: str,
        unit_price_minor: int | None = None,
        unit_price_exp: int | None = None,
        currency: str | None = None,
        notes: str | None = None,
    ) -> PurchaseOrderItem:
        """Add a line item to a PO. Caller must commit.

        Raises:
            PONotFoundError: po_id not found or wrong restaurant.
            POStatusError:   PO is not in draft status.
            ValueError:      quantity ≤ 0.
        """
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")

        po = self._require_po(po_id, restaurant_id)
        if po.status != _DRAFT:
            raise POStatusError(
                f"Can only add items to draft POs. PO {po_id} is '{po.status}'."
            )

        item = PurchaseOrderItem(
            po_id=po_id,
            inventory_item_id=inventory_item_id,
            item_name=item_name.strip(),
            quantity=float(quantity),
            unit=unit.strip(),
            unit_price_minor=unit_price_minor,
            unit_price_exp=unit_price_exp,
            currency=currency,
            notes=notes,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def remove_item(
        self,
        po_id: uuid.UUID,
        item_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> None:
        """Remove a line item from a draft PO. Caller must commit.

        Raises:
            PONotFoundError: po_id not found or wrong restaurant.
            POStatusError:   PO is not in draft status.
        """
        po = self._require_po(po_id, restaurant_id)
        if po.status != _DRAFT:
            raise POStatusError(f"Can only remove items from draft POs. PO is '{po.status}'.")

        item = self.session.get(PurchaseOrderItem, item_id)
        if item is not None and item.po_id == po_id:
            self.session.delete(item)

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def submit(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> PurchaseOrder:
        """Transition draft → sent. Sets sent_at. Caller must commit.

        Raises:
            PONotFoundError: not found.
            POStatusError:   not in draft.
        """
        po = self._require_po(po_id, restaurant_id)
        if po.status != _DRAFT:
            raise POStatusError(f"submit() requires draft status, got '{po.status}'.")
        po.status = _SENT
        po.sent_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        logger.info("purchase_order_service submitted po_id=%s", po_id)
        return po

    def cancel(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> PurchaseOrder:
        """Transition draft|sent → cancelled. Caller must commit.

        Raises:
            PONotFoundError: not found.
            POStatusError:   already received — cannot cancel.
        """
        po = self._require_po(po_id, restaurant_id)
        if po.status == _RECEIVED:
            raise POStatusError("Cannot cancel a received PO.")
        if po.status == _CANCELLED:
            raise POStatusError("PO is already cancelled.")
        po.status = _CANCELLED
        self.session.flush()
        logger.info("purchase_order_service cancelled po_id=%s", po_id)
        return po

    def mark_received(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> PurchaseOrder:
        """Transition sent → received. Sets received_at. Caller must commit.

        Raises:
            PONotFoundError: not found.
            POStatusError:   not in sent status.
        """
        po = self._require_po(po_id, restaurant_id)
        if po.status != _SENT:
            raise POStatusError(
                f"mark_received() requires sent status, got '{po.status}'."
            )
        po.status = _RECEIVED
        po.received_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        logger.info("purchase_order_service received po_id=%s", po_id)
        return po

    # ------------------------------------------------------------------
    # Read queries
    # ------------------------------------------------------------------

    def list_orders(
        self,
        restaurant_id: uuid.UUID,
        status: str | None = None,
        offset: int = 0,
        limit: int = 10,
    ) -> list[PurchaseOrder]:
        """Paginated POs for a restaurant, newest first.

        If status is provided, filter to that status only.
        """
        stmt = (
            select(PurchaseOrder)
            .where(PurchaseOrder.restaurant_id == restaurant_id)
            .order_by(PurchaseOrder.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(PurchaseOrder.status == status)
        return list(self.session.scalars(stmt).all())

    def count_orders(
        self,
        restaurant_id: uuid.UUID,
        status: str | None = None,
    ) -> int:
        """Total PO count, optionally filtered by status."""
        stmt = select(func.count()).where(
            PurchaseOrder.restaurant_id == restaurant_id
        )
        if status is not None:
            stmt = stmt.where(PurchaseOrder.status == status)
        return self.session.scalar(stmt) or 0

    def get_order_with_items(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> tuple[PurchaseOrder, list[PurchaseOrderItem]]:
        """Return (PurchaseOrder, items) for a given PO.

        Raises:
            PONotFoundError: not found or wrong restaurant.
        """
        po = self._require_po(po_id, restaurant_id)
        items_stmt = (
            select(PurchaseOrderItem)
            .where(PurchaseOrderItem.po_id == po_id)
            .order_by(PurchaseOrderItem.created_at)
        )
        items = list(self.session.scalars(items_stmt).all())
        return po, items

    def get_spend_summary(
        self,
        restaurant_id: uuid.UUID,
        since: dt.datetime,
    ) -> SpendSummary:
        """Aggregate spend on received POs since `since`.

        Only counts received POs. Groups by supplier_name (uses snapshot,
        not live supplier.name). Computes totals in Python via
        unit_price_minor/10^unit_price_exp * quantity. Items without
        unit_price_minor are included in order_count but excluded from total.

        Mixed currencies: groups by (supplier_name, currency).
        Returns rows sorted by total_display descending.
        """
        # Get all received POs + items since the cutoff
        pos_stmt = (
            select(PurchaseOrder)
            .where(
                PurchaseOrder.restaurant_id == restaurant_id,
                PurchaseOrder.status == _RECEIVED,
                PurchaseOrder.received_at >= since,
            )
        )
        pos = list(self.session.scalars(pos_stmt).all())

        if not pos:
            period_label = since.strftime("%b %Y")
            return SpendSummary(period_label=period_label)

        po_ids = [po.id for po in pos]
        items_stmt = select(PurchaseOrderItem).where(
            PurchaseOrderItem.po_id.in_(po_ids)
        )
        all_items = list(self.session.scalars(items_stmt).all())

        # Map po_id → PO for quick lookup
        po_by_id = {po.id: po for po in pos}

        # Aggregate: (supplier_name, currency) → (total, count)
        agg: dict[tuple[str, str], list] = {}
        for item in all_items:
            po = po_by_id[item.po_id]
            key = (po.supplier_name, item.currency or "—")
            if key not in agg:
                agg[key] = [Decimal("0"), po.supplier_id, set()]
            agg[key][2].add(po.id)
            if item.unit_price_minor is not None and item.unit_price_exp is not None:
                unit_price = Decimal(item.unit_price_minor) / Decimal(
                    10 ** item.unit_price_exp
                )
                agg[key][0] += unit_price * Decimal(str(item.quantity))

        rows: list[SpendRow] = []
        grand_total = Decimal("0")
        for (sup_name, currency), (total, sup_id, po_set) in agg.items():
            rows.append(
                SpendRow(
                    supplier_name=sup_name,
                    supplier_id=sup_id,
                    total_display=total.quantize(Decimal("0.01")),
                    currency=currency,
                    order_count=len(po_set),
                )
            )
            grand_total += total

        rows.sort(key=lambda r: r.total_display, reverse=True)
        period_label = since.strftime("%b %Y")

        return SpendSummary(
            period_label=period_label,
            rows=rows,
            grand_total=grand_total.quantize(Decimal("0.01")),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_po(
        self,
        po_id: uuid.UUID,
        restaurant_id: uuid.UUID,
    ) -> PurchaseOrder:
        """Return PO if found and belongs to restaurant. Raise PONotFoundError otherwise."""
        po = self.session.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.restaurant_id == restaurant_id,
            )
        )
        if po is None:
            raise PONotFoundError(
                f"PurchaseOrder {po_id} not found for restaurant {restaurant_id}"
            )
        return po
