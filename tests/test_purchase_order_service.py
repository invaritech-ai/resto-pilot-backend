"""Unit tests for app/services/purchase_order_service.py.

All tests use mocked SQLAlchemy sessions — no real DB required.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.purchase_order_service import (
    PONotFoundError,
    POStatusError,
    PurchaseOrderService,
    SpendSummary,
)

RESTAURANT_ID = uuid.uuid4()
SUPPLIER_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> MagicMock:
    return MagicMock()


def _make_po(
    status: str = "draft",
    po_id: uuid.UUID | None = None,
    supplier_name: str = "Cheong Hing",
) -> MagicMock:
    po = MagicMock()
    po.id = po_id or PO_ID
    po.restaurant_id = RESTAURANT_ID
    po.supplier_id = SUPPLIER_ID
    po.supplier_name = supplier_name
    po.status = status
    po.sent_at = None
    po.received_at = None
    return po


def _make_po_item(
    quantity: float = 3.0,
    unit_price_minor: int | None = 1250,
    unit_price_exp: int | None = 2,
    currency: str | None = "SGD",
) -> MagicMock:
    item = MagicMock()
    item.id = uuid.uuid4()
    item.po_id = PO_ID
    item.inventory_item_id = ITEM_ID
    item.item_name = "Chicken Breast"
    item.quantity = Decimal(str(quantity))
    item.unit = "kg"
    item.unit_price_minor = unit_price_minor
    item.unit_price_exp = unit_price_exp
    item.currency = currency
    return item


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------


class TestCreateDraft:
    def test_creates_po(self):
        session = _make_session()
        svc = PurchaseOrderService(session)
        po = svc.create_draft(RESTAURANT_ID, SUPPLIER_ID, "Cheong Hing", USER_ID)

        session.add.assert_called_once()
        session.flush.assert_called_once()
        # Verify the added object is a PurchaseOrder
        added = session.add.call_args[0][0]
        assert added.status == "draft"
        assert added.supplier_name == "Cheong Hing"


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------


class TestAddItem:
    def test_adds_item_to_draft(self):
        session = _make_session()
        po = _make_po(status="draft")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        item = svc.add_item(
            po_id=PO_ID,
            restaurant_id=RESTAURANT_ID,
            inventory_item_id=ITEM_ID,
            item_name="Chicken",
            quantity=Decimal("3"),
            unit="kg",
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    def test_raises_if_po_not_found(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = PurchaseOrderService(session)
        with pytest.raises(PONotFoundError):
            svc.add_item(PO_ID, RESTAURANT_ID, None, "Eggs", Decimal("1"), "tray")

    def test_raises_if_not_draft(self):
        session = _make_session()
        po = _make_po(status="sent")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        with pytest.raises(POStatusError, match="draft"):
            svc.add_item(PO_ID, RESTAURANT_ID, None, "Eggs", Decimal("1"), "tray")

    def test_raises_on_zero_quantity(self):
        session = _make_session()
        po = _make_po(status="draft")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        with pytest.raises(ValueError, match="quantity must be positive"):
            svc.add_item(PO_ID, RESTAURANT_ID, None, "Eggs", Decimal("0"), "tray")


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_transitions_draft_to_sent(self):
        session = _make_session()
        po = _make_po(status="draft")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        result = svc.submit(PO_ID, RESTAURANT_ID)

        assert result.status == "sent"
        assert result.sent_at is not None
        session.flush.assert_called_once()

    def test_raises_if_not_draft(self):
        session = _make_session()
        po = _make_po(status="sent")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        with pytest.raises(POStatusError, match="draft"):
            svc.submit(PO_ID, RESTAURANT_ID)

    def test_raises_if_not_found(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = PurchaseOrderService(session)
        with pytest.raises(PONotFoundError):
            svc.submit(PO_ID, RESTAURANT_ID)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancels_draft(self):
        session = _make_session()
        po = _make_po(status="draft")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        result = svc.cancel(PO_ID, RESTAURANT_ID)

        assert result.status == "cancelled"

    def test_cancels_sent(self):
        session = _make_session()
        po = _make_po(status="sent")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        result = svc.cancel(PO_ID, RESTAURANT_ID)
        assert result.status == "cancelled"

    def test_raises_if_received(self):
        session = _make_session()
        po = _make_po(status="received")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        with pytest.raises(POStatusError, match="Cannot cancel a received PO"):
            svc.cancel(PO_ID, RESTAURANT_ID)


# ---------------------------------------------------------------------------
# mark_received
# ---------------------------------------------------------------------------


class TestMarkReceived:
    def test_transitions_sent_to_received(self):
        session = _make_session()
        po = _make_po(status="sent")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        result = svc.mark_received(PO_ID, RESTAURANT_ID)

        assert result.status == "received"
        assert result.received_at is not None

    def test_raises_if_not_sent(self):
        session = _make_session()
        po = _make_po(status="draft")
        session.scalar.return_value = po

        svc = PurchaseOrderService(session)
        with pytest.raises(POStatusError, match="sent"):
            svc.mark_received(PO_ID, RESTAURANT_ID)


# ---------------------------------------------------------------------------
# list_orders / count_orders
# ---------------------------------------------------------------------------


class TestListOrders:
    def test_returns_list(self):
        session = _make_session()
        po1 = _make_po(status="draft")
        po2 = _make_po(status="sent")
        session.scalars.return_value.all.return_value = [po1, po2]

        svc = PurchaseOrderService(session)
        result = svc.list_orders(RESTAURANT_ID)

        assert result == [po1, po2]

    def test_count_returns_zero_when_empty(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = PurchaseOrderService(session)
        assert svc.count_orders(RESTAURANT_ID) == 0


# ---------------------------------------------------------------------------
# ownership guard
# ---------------------------------------------------------------------------


class TestOwnershipGuard:
    def test_add_item_rejects_wrong_restaurant(self):
        session = _make_session()
        # PO not found for this restaurant (different restaurant_id)
        session.scalar.return_value = None

        svc = PurchaseOrderService(session)
        with pytest.raises(PONotFoundError):
            svc.add_item(PO_ID, uuid.uuid4(), None, "Eggs", Decimal("1"), "tray")


# ---------------------------------------------------------------------------
# get_spend_summary
# ---------------------------------------------------------------------------


class TestGetSpendSummary:
    def test_returns_empty_when_no_received_pos(self):
        session = _make_session()
        session.scalars.return_value.all.return_value = []

        svc = PurchaseOrderService(session)
        since = dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc)
        summary = svc.get_spend_summary(RESTAURANT_ID, since)

        assert isinstance(summary, SpendSummary)
        assert summary.rows == []
        assert summary.grand_total == Decimal("0")

    def test_aggregates_received_pos(self):
        session = _make_session()
        po = _make_po(status="received")
        po.received_at = dt.datetime(2026, 2, 10, tzinfo=dt.timezone.utc)
        item = _make_po_item(quantity=3.0, unit_price_minor=1250, unit_price_exp=2, currency="SGD")

        # First scalars call → POs; second scalars call → items
        session.scalars.return_value.all.side_effect = [[po], [item]]

        svc = PurchaseOrderService(session)
        since = dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc)
        summary = svc.get_spend_summary(RESTAURANT_ID, since)

        # 3 kg × $12.50/kg = $37.50
        assert len(summary.rows) == 1
        assert summary.rows[0].supplier_name == "Cheong Hing"
        assert summary.rows[0].total_display == Decimal("37.50")
        assert summary.grand_total == Decimal("37.50")
