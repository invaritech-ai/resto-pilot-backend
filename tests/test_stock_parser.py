"""Unit tests for app/services/stock_parser.py."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.stock_parser import StockPhrase, parse_stock_phrase


# ---------------------------------------------------------------------------
# "use" kind — debit
# ---------------------------------------------------------------------------


class TestUsePattern:
    def test_used_with_unit(self):
        p = parse_stock_phrase("used 1kg onion")
        assert p == StockPhrase(item_name="onion", qty=Decimal("1"), unit="kg", kind="use")

    def test_use_without_d(self):
        p = parse_stock_phrase("use 1kg onion")
        assert p is not None
        assert p.kind == "use"
        assert p.item_name == "onion"

    def test_used_decimal(self):
        p = parse_stock_phrase("used 1.5 kg chicken breast")
        assert p == StockPhrase(item_name="chicken breast", qty=Decimal("1.5"), unit="kg", kind="use")

    def test_used_comma_decimal(self):
        p = parse_stock_phrase("used 1,5 kg onion")
        assert p is not None
        assert p.qty == Decimal("1.5")

    def test_used_grams(self):
        p = parse_stock_phrase("used 500g tomato")
        assert p is not None
        assert p.qty == Decimal("500")
        assert p.unit == "g"
        assert p.item_name == "tomato"

    def test_used_no_unit(self):
        p = parse_stock_phrase("used 3 eggs")
        assert p is not None
        assert p.unit is None
        assert p.item_name == "eggs"
        assert p.qty == Decimal("3")

    def test_used_multi_word_item_no_unit(self):
        p = parse_stock_phrase("used 2 chicken breast")
        assert p is not None
        assert p.item_name == "chicken breast"
        assert p.unit is None

    def test_used_liters(self):
        p = parse_stock_phrase("used 2 liters cooking oil")
        assert p is not None
        assert p.unit == "L"
        assert p.item_name == "cooking oil"

    def test_used_ml(self):
        p = parse_stock_phrase("used 250ml soy sauce")
        assert p is not None
        assert p.unit == "ml"
        assert p.item_name == "soy sauce"

    def test_used_pcs(self):
        p = parse_stock_phrase("used 6 pcs eggs")
        assert p is not None
        assert p.unit == "pcs"
        assert p.item_name == "eggs"

    def test_used_case_insensitive(self):
        p = parse_stock_phrase("USED 1KG ONION")
        assert p is not None
        assert p.item_name == "ONION"
        assert p.unit == "kg"


# ---------------------------------------------------------------------------
# "reconcile" kind — set balance
# ---------------------------------------------------------------------------


class TestReconcilePattern:
    def test_left_basic(self):
        p = parse_stock_phrase("1kg onion left")
        assert p == StockPhrase(item_name="onion", qty=Decimal("1"), unit="kg", kind="reconcile")

    def test_remaining_keyword(self):
        p = parse_stock_phrase("500g tomato remaining")
        assert p is not None
        assert p.kind == "reconcile"
        assert p.qty == Decimal("500")
        assert p.unit == "g"

    def test_rem_keyword(self):
        p = parse_stock_phrase("2kg chicken rem")
        assert p is not None
        assert p.kind == "reconcile"

    def test_left_decimal(self):
        p = parse_stock_phrase("1.5 kg chicken breast left")
        assert p is not None
        assert p.qty == Decimal("1.5")
        assert p.item_name == "chicken breast"

    def test_left_comma_decimal(self):
        p = parse_stock_phrase("1,5 kg onion left")
        assert p is not None
        assert p.qty == Decimal("1.5")

    def test_left_no_unit(self):
        p = parse_stock_phrase("5 lettuce left")
        assert p is not None
        assert p.unit is None
        assert p.item_name == "lettuce"
        assert p.qty == Decimal("5")

    def test_left_liters(self):
        p = parse_stock_phrase("3 liters oil left")
        assert p is not None
        assert p.unit == "L"
        assert p.item_name == "oil"

    def test_left_case_insensitive(self):
        p = parse_stock_phrase("1KG ONION LEFT")
        assert p is not None
        assert p.kind == "reconcile"


# ---------------------------------------------------------------------------
# Non-matching inputs → None
# ---------------------------------------------------------------------------


class TestNonMatching:
    def test_plain_query(self):
        assert parse_stock_phrase("show my inventory") is None

    def test_no_keyword(self):
        # "onion 1kg" has no "used"/"left" keyword
        assert parse_stock_phrase("onion 1kg") is None

    def test_command(self):
        assert parse_stock_phrase("/inventory") is None

    def test_empty(self):
        assert parse_stock_phrase("") is None

    def test_whitespace_only(self):
        assert parse_stock_phrase("   ") is None

    def test_number_only(self):
        assert parse_stock_phrase("1kg") is None

    def test_natural_language(self):
        assert parse_stock_phrase("what is my stock level?") is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zero_qty_returns_none(self):
        assert parse_stock_phrase("used 0 onion") is None

    def test_zero_qty_left_returns_none(self):
        assert parse_stock_phrase("0kg onion left") is None

    def test_unit_without_item_returns_none(self):
        # "use 1 kg" has no item after the unit
        assert parse_stock_phrase("use 1 kg") is None

    def test_used_only_returns_none(self):
        assert parse_stock_phrase("used") is None

    def test_unknown_unit_treated_as_item_prefix(self):
        # "box" is not in UNIT_ALIASES → becomes part of item name
        p = parse_stock_phrase("used 2 box tomatoes")
        assert p is not None
        assert p.unit is None
        assert p.item_name == "box tomatoes"

    def test_whitespace_stripped(self):
        p = parse_stock_phrase("  used 1kg onion  ")
        assert p is not None
        assert p.item_name == "onion"
