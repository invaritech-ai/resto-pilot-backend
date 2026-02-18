"""Unit tests for app/services/inventory_service.py.

All tests use mocked SQLAlchemy sessions — no real DB required.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.inventory_service import (
    BalanceSummary,
    InventoryService,
    ItemNotFoundError,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

RESTAURANT_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _make_session() -> MagicMock:
    return MagicMock()


def _make_item(name: str = "Chicken Breast", item_id: uuid.UUID | None = None) -> MagicMock:
    item = MagicMock()
    item.id = item_id or ITEM_ID
    item.name = name
    item.name_lower = name.lower()
    item.restaurant_id = RESTAURANT_ID
    item.unit = "kg"
    item.supplier_id = None
    return item


def _make_balance(balance_val: float = 10.0, item_id: uuid.UUID | None = None) -> MagicMock:
    b = MagicMock()
    b.item_id = item_id or ITEM_ID
    b.restaurant_id = RESTAURANT_ID
    b.balance = Decimal(str(balance_val))
    b.last_txn_id = uuid.uuid4()
    return b


def _make_txn(txn_type: str = "credit", quantity: float = 5.0) -> MagicMock:
    t = MagicMock()
    t.id = uuid.uuid4()
    t.txn_type = txn_type
    t.quantity = Decimal(str(quantity))
    t.source = "invoice"
    return t


# ---------------------------------------------------------------------------
# get_or_create_item
# ---------------------------------------------------------------------------


class TestGetOrCreateItem:
    def test_returns_existing_item_when_found(self):
        session = _make_session()
        existing = _make_item("Chicken Breast")
        session.scalar.return_value = existing

        svc = InventoryService(session)
        item, created = svc.get_or_create_item(RESTAURANT_ID, "Chicken Breast")

        assert item is existing
        assert created is False
        session.add.assert_not_called()

    def test_creates_item_when_not_found(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = InventoryService(session)
        item, created = svc.get_or_create_item(RESTAURANT_ID, "New Ingredient", unit="L")

        assert created is True
        session.add.assert_called_once()
        session.flush.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.name == "New Ingredient"
        assert added.name_lower == "new ingredient"
        assert added.unit == "L"
        assert added.restaurant_id == RESTAURANT_ID

    def test_strips_whitespace_from_name(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = InventoryService(session)
        _, created = svc.get_or_create_item(RESTAURANT_ID, "  Olive Oil  ")

        added = session.add.call_args[0][0]
        assert added.name == "Olive Oil"
        assert added.name_lower == "olive oil"


# ---------------------------------------------------------------------------
# record_transaction
# ---------------------------------------------------------------------------


class TestRecordTransaction:
    def _setup_new_balance(self, session: MagicMock) -> MagicMock:
        """Configure session so no existing balance is found."""
        session.flush = MagicMock()
        session.commit = MagicMock()
        txn = _make_txn()

        # scalar: first call for InventoryBalance → None (new balance)
        session.scalar.return_value = None

        return txn

    def test_credit_creates_balance_when_none_exists(self):
        session = _make_session()
        self._setup_new_balance(session)

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="credit",
            quantity=5.0,
            created_by=USER_ID,
        )

        # Two adds: txn + new balance
        assert session.add.call_count == 2
        txn_obj = session.add.call_args_list[0][0][0]
        balance_obj = session.add.call_args_list[1][0][0]
        assert txn_obj.txn_type == "credit"
        assert float(txn_obj.quantity) == 5.0
        assert float(balance_obj.balance) == 5.0
        session.commit.assert_called_once()

    def test_debit_creates_negative_balance_when_none_exists(self):
        session = _make_session()
        self._setup_new_balance(session)

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="debit",
            quantity=3.0,
            created_by=USER_ID,
        )

        balance_obj = session.add.call_args_list[1][0][0]
        assert float(balance_obj.balance) == -3.0

    def test_credit_increments_existing_balance(self):
        session = _make_session()
        session.flush = MagicMock()
        session.commit = MagicMock()

        existing_balance = _make_balance(balance_val=10.0)
        session.scalar.return_value = existing_balance

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="credit",
            quantity=5.0,
            created_by=USER_ID,
        )

        assert float(existing_balance.balance) == 15.0
        session.commit.assert_called_once()

    def test_debit_decrements_existing_balance(self):
        session = _make_session()
        session.flush = MagicMock()
        session.commit = MagicMock()

        existing_balance = _make_balance(balance_val=10.0)
        session.scalar.return_value = existing_balance

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="debit",
            quantity=4.0,
            created_by=USER_ID,
        )

        assert float(existing_balance.balance) == 6.0

    def test_debit_allows_negative_balance(self):
        session = _make_session()
        session.flush = MagicMock()
        session.commit = MagicMock()

        existing_balance = _make_balance(balance_val=2.0)
        session.scalar.return_value = existing_balance

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="debit",
            quantity=5.0,
            created_by=USER_ID,
        )

        assert float(existing_balance.balance) == -3.0

    def test_invalid_txn_type_raises(self):
        session = _make_session()
        svc = InventoryService(session)

        with pytest.raises(ValueError, match="txn_type"):
            svc.record_transaction(
                restaurant_id=RESTAURANT_ID,
                item_id=ITEM_ID,
                txn_type="wrong",
                quantity=1.0,
                created_by=USER_ID,
            )

    def test_zero_quantity_raises(self):
        session = _make_session()
        svc = InventoryService(session)

        with pytest.raises(ValueError, match="quantity"):
            svc.record_transaction(
                restaurant_id=RESTAURANT_ID,
                item_id=ITEM_ID,
                txn_type="credit",
                quantity=0.0,
                created_by=USER_ID,
            )

    def test_negative_quantity_raises(self):
        session = _make_session()
        svc = InventoryService(session)

        with pytest.raises(ValueError, match="quantity"):
            svc.record_transaction(
                restaurant_id=RESTAURANT_ID,
                item_id=ITEM_ID,
                txn_type="credit",
                quantity=-1.0,
                created_by=USER_ID,
            )

    def test_staging_id_and_optional_fields_stored(self):
        session = _make_session()
        session.flush = MagicMock()
        session.commit = MagicMock()
        session.scalar.return_value = None

        staging_id = uuid.uuid4()

        svc = InventoryService(session)
        svc.record_transaction(
            restaurant_id=RESTAURANT_ID,
            item_id=ITEM_ID,
            txn_type="credit",
            quantity=10.0,
            created_by=USER_ID,
            source="invoice",
            unit_price=8.5,
            amount=85.0,
            staging_id=staging_id,
            notes="First delivery",
        )

        txn_obj = session.add.call_args_list[0][0][0]
        assert txn_obj.staging_id == staging_id
        assert float(txn_obj.unit_price) == 8.5
        assert float(txn_obj.amount) == 85.0
        assert txn_obj.notes == "First delivery"
        assert txn_obj.source == "invoice"


# ---------------------------------------------------------------------------
# list_items / count_items
# ---------------------------------------------------------------------------


class TestListItems:
    def test_list_items_returns_pairs(self):
        session = _make_session()
        item = _make_item()
        balance = _make_balance()

        mock_rows = [MagicMock()]
        mock_rows[0].__getitem__ = lambda self, i: [item, balance][i]
        session.execute.return_value.all.return_value = mock_rows

        svc = InventoryService(session)
        result = svc.list_items(RESTAURANT_ID)

        assert len(result) == 1
        assert result[0][0] is item
        assert result[0][1] is balance

    def test_count_items_returns_scalar(self):
        session = _make_session()
        session.scalar.return_value = 7

        svc = InventoryService(session)
        assert svc.count_items(RESTAURANT_ID) == 7

    def test_count_items_returns_zero_when_none(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = InventoryService(session)
        assert svc.count_items(RESTAURANT_ID) == 0


# ---------------------------------------------------------------------------
# get_item_detail
# ---------------------------------------------------------------------------


class TestGetItemDetail:
    def test_raises_when_item_not_found(self):
        session = _make_session()
        session.get.return_value = None

        svc = InventoryService(session)
        with pytest.raises(ItemNotFoundError):
            svc.get_item_detail(ITEM_ID)

    def test_returns_item_balance_and_last_txn(self):
        session = _make_session()
        item = _make_item()
        balance = _make_balance()
        txn = _make_txn()

        last_txn_id = uuid.uuid4()
        balance.last_txn_id = last_txn_id

        def _get(model_class, pk):
            if model_class.__name__ == "InventoryItem":
                return item
            if model_class.__name__ == "InventoryTransaction":
                return txn
            return None

        session.get.side_effect = _get
        session.scalar.return_value = balance

        svc = InventoryService(session)
        returned_item, returned_balance, returned_txn = svc.get_item_detail(ITEM_ID)

        assert returned_item is item
        assert returned_balance is balance
        assert returned_txn is txn

    def test_returns_none_txn_when_no_balance(self):
        session = _make_session()
        item = _make_item()

        session.get.return_value = item
        session.scalar.return_value = None

        svc = InventoryService(session)
        _, balance, txn = svc.get_item_detail(ITEM_ID)

        assert balance is None
        assert txn is None


# ---------------------------------------------------------------------------
# get_balance_summary
# ---------------------------------------------------------------------------


class TestGetBalanceSummary:
    def test_summary_counts_all_categories(self):
        session = _make_session()
        # scalar calls: count_items → 10, zero → 2, negative → 1
        session.scalar.side_effect = [10, 2, 1]

        svc = InventoryService(session)
        summary = svc.get_balance_summary(RESTAURANT_ID)

        assert isinstance(summary, BalanceSummary)
        assert summary.total_items == 10
        assert summary.zero_stock_count == 2
        assert summary.negative_count == 1

    def test_summary_all_zeros_when_no_items(self):
        session = _make_session()
        session.scalar.side_effect = [0, 0, 0]

        svc = InventoryService(session)
        summary = svc.get_balance_summary(RESTAURANT_ID)

        assert summary.total_items == 0
        assert summary.zero_stock_count == 0
        assert summary.negative_count == 0

    def test_summary_handles_none_scalars(self):
        session = _make_session()
        session.scalar.side_effect = [None, None, None]

        svc = InventoryService(session)
        summary = svc.get_balance_summary(RESTAURANT_ID)

        assert summary.total_items == 0
        assert summary.zero_stock_count == 0
        assert summary.negative_count == 0


# ---------------------------------------------------------------------------
# fuzzy_match_item
# ---------------------------------------------------------------------------


class TestFuzzyMatchItem:
    def test_returns_sorted_matches(self):
        session = _make_session()
        item1 = _make_item("Chicken Breast")
        item2 = _make_item("Chicken Wings")

        row1 = MagicMock()
        row1.__getitem__ = lambda self, i: [item1, 0.9][i]
        row2 = MagicMock()
        row2.__getitem__ = lambda self, i: [item2, 0.75][i]

        session.execute.return_value.all.return_value = [row1, row2]

        svc = InventoryService(session)
        results = svc.fuzzy_match_item(RESTAURANT_ID, "chicken breast")

        assert len(results) == 2
        assert results[0][1] == 0.9
        assert results[1][1] == 0.75

    def test_returns_empty_when_no_matches(self):
        session = _make_session()
        session.execute.return_value.all.return_value = []

        svc = InventoryService(session)
        results = svc.fuzzy_match_item(RESTAURANT_ID, "xyz unknown")

        assert results == []
