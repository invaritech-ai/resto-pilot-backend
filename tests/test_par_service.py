"""Unit tests for app/services/par_service.py.

All tests use mocked SQLAlchemy sessions — no real DB required.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.par_service import BelowParItem, ParLevelNotFoundError, ParService

RESTAURANT_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_session() -> MagicMock:
    return MagicMock()


def _make_item(name: str = "Chicken Breast", item_id: uuid.UUID | None = None) -> MagicMock:
    item = MagicMock()
    item.id = item_id or ITEM_ID
    item.name = name
    item.name_lower = name.lower()
    item.restaurant_id = RESTAURANT_ID
    item.unit = "kg"
    return item


def _make_par(par_qty: float = 5.0, unit: str = "kg") -> MagicMock:
    par = MagicMock()
    par.id = uuid.uuid4()
    par.restaurant_id = RESTAURANT_ID
    par.inventory_item_id = ITEM_ID
    par.par_qty = Decimal(str(par_qty))
    par.unit = unit
    return par


def _make_balance(balance_val: float = 2.0) -> MagicMock:
    b = MagicMock()
    b.item_id = ITEM_ID
    b.restaurant_id = RESTAURANT_ID
    b.balance = Decimal(str(balance_val))
    return b


# ---------------------------------------------------------------------------
# set_par
# ---------------------------------------------------------------------------


class TestSetPar:
    def test_set_par_creates_new(self):
        session = _make_session()
        item = _make_item()
        par = _make_par()

        # scalar: first call = ownership check (returns item), second call = existing lookup (None)
        session.scalar.side_effect = [item, None]
        result_par = MagicMock()
        session.execute.return_value.scalar_one.return_value = result_par

        svc = ParService(session)
        returned_par, created = svc.set_par(RESTAURANT_ID, ITEM_ID, Decimal("5.0"), "kg", USER_ID)

        assert created is True
        assert returned_par is result_par
        session.execute.assert_called_once()

    def test_set_par_updates_existing(self):
        session = _make_session()
        item = _make_item()
        existing_par = _make_par()

        # scalar: first call = ownership check (returns item), second call = existing lookup (returns par)
        session.scalar.side_effect = [item, existing_par]
        result_par = MagicMock()
        session.execute.return_value.scalar_one.return_value = result_par

        svc = ParService(session)
        returned_par, created = svc.set_par(RESTAURANT_ID, ITEM_ID, Decimal("5.0"), "kg", USER_ID)

        assert created is False
        assert returned_par is result_par

    def test_set_par_raises_on_zero_qty(self):
        session = _make_session()
        svc = ParService(session)

        with pytest.raises(ValueError, match="par_qty must be positive"):
            svc.set_par(RESTAURANT_ID, ITEM_ID, Decimal("0"), "kg", USER_ID)

    def test_set_par_raises_on_negative_qty(self):
        session = _make_session()
        svc = ParService(session)

        with pytest.raises(ValueError, match="par_qty must be positive"):
            svc.set_par(RESTAURANT_ID, ITEM_ID, Decimal("-1"), "kg", USER_ID)

    def test_set_par_raises_on_wrong_restaurant(self):
        session = _make_session()
        # Ownership guard returns None — item not in restaurant
        session.scalar.return_value = None

        svc = ParService(session)
        with pytest.raises(ValueError, match="does not belong to restaurant"):
            svc.set_par(RESTAURANT_ID, ITEM_ID, Decimal("5.0"), "kg", USER_ID)


# ---------------------------------------------------------------------------
# get_par_level
# ---------------------------------------------------------------------------


class TestGetParLevel:
    def test_returns_par_when_found(self):
        session = _make_session()
        par = _make_par()
        session.scalar.return_value = par

        svc = ParService(session)
        result = svc.get_par_level(RESTAURANT_ID, ITEM_ID)

        assert result is par

    def test_returns_none_when_not_found(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = ParService(session)
        result = svc.get_par_level(RESTAURANT_ID, ITEM_ID)

        assert result is None


# ---------------------------------------------------------------------------
# count_par_levels
# ---------------------------------------------------------------------------


class TestCountParLevels:
    def test_returns_zero_when_empty(self):
        session = _make_session()
        session.scalar.return_value = None

        svc = ParService(session)
        assert svc.count_par_levels(RESTAURANT_ID) == 0

    def test_returns_count(self):
        session = _make_session()
        session.scalar.return_value = 3

        svc = ParService(session)
        assert svc.count_par_levels(RESTAURANT_ID) == 3


# ---------------------------------------------------------------------------
# get_below_par_items
# ---------------------------------------------------------------------------


class TestGetBelowParItems:
    def test_returns_empty_when_all_at_par(self):
        session = _make_session()
        item = _make_item()
        par = _make_par(par_qty=5.0)
        balance = _make_balance(balance_val=6.0)  # above par

        session.execute.return_value.all.return_value = [(par, item, balance)]

        svc = ParService(session)
        results = svc.get_below_par_items(RESTAURANT_ID)

        assert results == []

    def test_returns_item_below_par(self):
        session = _make_session()
        item = _make_item()
        par = _make_par(par_qty=5.0)
        balance = _make_balance(balance_val=2.0)  # below par

        session.execute.return_value.all.return_value = [(par, item, balance)]

        svc = ParService(session)

        with patch.object(svc, "_find_best_price", return_value=None):
            results = svc.get_below_par_items(RESTAURANT_ID)

        assert len(results) == 1
        r = results[0]
        assert r.item is item
        assert r.balance == Decimal("2.0")
        assert r.par_qty == Decimal("5.0")
        assert r.gap == Decimal("3.0")
        assert r.best_supplier_name is None

    def test_includes_price_when_available(self):
        session = _make_session()
        item = _make_item()
        par = _make_par(par_qty=5.0)
        balance = _make_balance(balance_val=1.0)

        session.execute.return_value.all.return_value = [(par, item, balance)]

        mock_price = MagicMock()
        mock_price.price_minor = 1250
        mock_price.price_exp = 2
        mock_price.currency = "SGD"
        mock_sup_id = uuid.uuid4()

        svc = ParService(session)
        with patch.object(svc, "_find_best_price", return_value=(mock_price, "Cheong Hing", mock_sup_id)):
            results = svc.get_below_par_items(RESTAURANT_ID)

        assert len(results) == 1
        r = results[0]
        assert r.best_price_minor == 1250
        assert r.best_price_exp == 2
        assert r.best_price_currency == "SGD"
        assert r.best_supplier_name == "Cheong Hing"
        assert r.best_supplier_id == mock_sup_id

    def test_no_balance_row_treated_as_zero(self):
        session = _make_session()
        item = _make_item()
        par = _make_par(par_qty=3.0)

        # balance is None (item has never had a transaction)
        session.execute.return_value.all.return_value = [(par, item, None)]

        svc = ParService(session)
        with patch.object(svc, "_find_best_price", return_value=None):
            results = svc.get_below_par_items(RESTAURANT_ID)

        assert len(results) == 1
        assert results[0].balance == Decimal("0")
        assert results[0].gap == Decimal("3.0")

    def test_sorted_by_gap_descending(self):
        session = _make_session()
        item1 = _make_item("Onion", item_id=uuid.uuid4())
        item2 = _make_item("Chicken", item_id=uuid.uuid4())

        par1 = _make_par(par_qty=3.0)
        par1.inventory_item_id = item1.id
        par2 = _make_par(par_qty=10.0)
        par2.inventory_item_id = item2.id

        bal1 = _make_balance(balance_val=2.0)  # gap=1
        bal2 = _make_balance(balance_val=1.0)  # gap=9

        session.execute.return_value.all.return_value = [
            (par1, item1, bal1),
            (par2, item2, bal2),
        ]

        svc = ParService(session)
        with patch.object(svc, "_find_best_price", return_value=None):
            results = svc.get_below_par_items(RESTAURANT_ID)

        # Chicken has gap=9, Onion has gap=1 → Chicken first
        assert results[0].item is item2
        assert results[1].item is item1
