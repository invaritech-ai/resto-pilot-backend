"""Tests for app/services/money.py — the decimal ↔ minor-unit conversion layer."""

import pytest
from decimal import Decimal

from app.services.money import (
    DEFAULT_EXP,
    QTY_EXP,
    format_price,
    format_qty,
    infer_exp,
    to_display,
    to_minor,
)


# ---------------------------------------------------------------------------
# infer_exp
# ---------------------------------------------------------------------------

class TestInferExp:
    def test_known_two_decimal_currencies(self):
        for code in ("SGD", "USD", "EUR", "GBP", "AUD", "MYR", "THB"):
            assert infer_exp(code) == 2, code

    def test_zero_decimal_currencies(self):
        for code in ("JPY", "KRW", "VND"):
            assert infer_exp(code) == 0, code

    def test_three_decimal_currencies(self):
        for code in ("KWD", "JOD", "BHD", "OMR"):
            assert infer_exp(code) == 3, code

    def test_case_insensitive(self):
        assert infer_exp("sgd") == 2
        assert infer_exp("Jpy") == 0
        assert infer_exp("kwd") == 3

    def test_strips_whitespace(self):
        assert infer_exp("  USD  ") == 2

    def test_unknown_currency_returns_default(self):
        assert infer_exp("XYZ") == DEFAULT_EXP
        assert infer_exp("ABC") == DEFAULT_EXP

    def test_none_returns_default(self):
        assert infer_exp(None) == DEFAULT_EXP

    def test_empty_string_returns_default(self):
        assert infer_exp("") == DEFAULT_EXP


# ---------------------------------------------------------------------------
# to_minor
# ---------------------------------------------------------------------------

class TestToMinor:
    # -- basic conversions --
    def test_two_decimal_price(self):
        assert to_minor(2.50, 2) == 250

    def test_string_decimal(self):
        assert to_minor("2.50", 2) == 250

    def test_decimal_type(self):
        assert to_minor(Decimal("2.50"), 2) == 250

    def test_three_decimal_qty(self):
        assert to_minor(1.5, 3) == 1500

    def test_zero_exp_jpy(self):
        assert to_minor(500, 0) == 500

    def test_integer_value(self):
        assert to_minor(3, 2) == 300

    def test_zero_value(self):
        assert to_minor(0, 2) == 0

    def test_large_value(self):
        assert to_minor("9999.99", 2) == 999999

    def test_three_sig_figs(self):
        assert to_minor("1.234", 3) == 1234

    # -- rounding --
    def test_rounds_half_up(self):
        # 1.005 × 100 = 100.5 → rounds to 101
        assert to_minor("1.005", 2) == 101

    def test_rounds_down(self):
        # 1.004 × 100 = 100.4 → rounds to 100
        assert to_minor("1.004", 2) == 100

    # -- errors --
    def test_negative_value_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            to_minor(-1.0, 2)

    def test_negative_exp_raises(self):
        with pytest.raises(ValueError, match="exp must be >= 0"):
            to_minor(1.0, -1)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            to_minor("abc", 2)


# ---------------------------------------------------------------------------
# to_display
# ---------------------------------------------------------------------------

class TestToDisplay:
    def test_two_decimal(self):
        result = to_display(250, 2)
        assert result == Decimal("2.50")
        assert str(result) == "2.50"  # trailing zero preserved

    def test_three_decimal(self):
        result = to_display(1500, 3)
        assert result == Decimal("1.500")
        assert str(result) == "1.500"  # three decimal places preserved

    def test_zero_decimal_jpy(self):
        result = to_display(500, 0)
        assert result == Decimal("500")
        assert str(result) == "500"

    def test_zero_value(self):
        assert to_display(0, 2) == Decimal("0.00")

    def test_large_value(self):
        assert to_display(999999, 2) == Decimal("9999.99")

    def test_roundtrip(self):
        """to_minor → to_display should recover the original decimal."""
        original = Decimal("12.34")
        minor = to_minor(original, 2)
        recovered = to_display(minor, 2)
        assert recovered == original

    def test_roundtrip_three_decimal(self):
        original = Decimal("1.500")
        minor = to_minor(original, 3)
        assert minor == 1500
        assert to_display(minor, 3) == original

    def test_negative_exp_raises(self):
        with pytest.raises(ValueError, match="exp must be >= 0"):
            to_display(100, -1)


# ---------------------------------------------------------------------------
# format_price
# ---------------------------------------------------------------------------

class TestFormatPrice:
    def test_with_currency(self):
        assert format_price(250, 2, "SGD") == "2.50 SGD"

    def test_lowercase_currency_uppercased(self):
        assert format_price(250, 2, "sgd") == "2.50 SGD"

    def test_jpy_no_decimal(self):
        assert format_price(500, 0, "JPY") == "500 JPY"

    def test_kwd_three_decimal(self):
        assert format_price(1500, 3, "KWD") == "1.500 KWD"

    def test_without_currency(self):
        assert format_price(250, 2) == "2.50"


# ---------------------------------------------------------------------------
# format_qty
# ---------------------------------------------------------------------------

class TestFormatQty:
    def test_with_unit(self):
        assert format_qty(1500, 3, "kg") == "1.500 kg"

    def test_ltr(self):
        assert format_qty(1000, 3, "ltr") == "1.000 ltr"

    def test_without_unit(self):
        assert format_qty(5000, 3) == "5.000"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_exp(self):
        assert DEFAULT_EXP == 2

    def test_qty_exp(self):
        assert QTY_EXP == 3
