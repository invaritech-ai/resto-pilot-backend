"""
Decimal ↔ integer minor-unit conversion helpers.

Conversion boundary rule:
  - LLMs always speak in human-readable decimals.
  - Storage (DB + staging JSONB) is always integers: price_minor + price_exp.
  - to_minor() is called immediately after LLM output is validated (inbound).
  - to_display() is called immediately before sending to LLM or rendering (outbound).
  - Nothing else in the codebase touches Decimal or float for prices/quantities.

Examples:
  to_minor(2.50, exp=2)    → 250      (2.50 SGD stored as 250 with exp 2)
  to_minor(1.5,  exp=3)    → 1500     (1.5 kg stored as 1500 with exp 3)
  to_minor(500,  exp=0)    → 500      (500 JPY stored as 500 with exp 0)
  to_display(250,  exp=2)  → Decimal('2.50')
  to_display(1500, exp=3)  → Decimal('1.500')
  infer_exp("SGD")         → 2
  infer_exp("JPY")         → 0
  infer_exp("KWD")         → 3
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 minor unit exponents for currencies we're likely to encounter.
# Source: https://en.wikipedia.org/wiki/ISO_4217#Active_codes
_CURRENCY_EXP: dict[str, int] = {
    # 0 decimal places
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "IDR": 0,
    "BIF": 0,
    "CLP": 0,
    "GNF": 0,
    "ISK": 0,
    "MGA": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "XAF": 0,
    "XOF": 0,
    # 3 decimal places
    "KWD": 3,
    "JOD": 3,
    "BHD": 3,
    "OMR": 3,
    "TND": 3,
    # 2 decimal places (default — covers most currencies)
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "SGD": 2,
    "AUD": 2,
    "CAD": 2,
    "HKD": 2,
    "MYR": 2,
    "THB": 2,
    "PHP": 2,
    "INR": 2,
    "CNY": 2,
    "NZD": 2,
    "CHF": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "AED": 2,
    "SAR": 2,
    "QAR": 2,
    "TWD": 2,
    "ZAR": 2,
    "BRL": 2,
    "MXN": 2,
    "IDR": 2,  # technically 0 but practically traded in 2
}

# Fallback for unknown currencies.
DEFAULT_EXP = 2

# Unit quantities (kg, ltr, etc.) are always stored to 3 decimal places.
# e.g. 1.5 kg → unit_qty_minor=1500, unit_qty_exp=3
QTY_EXP = 3


def infer_exp(currency: str | None) -> int:
    """Return the standard minor-unit exponent for a currency code.

    Falls back to DEFAULT_EXP (2) for unknown or null currencies.
    """
    if not currency:
        return DEFAULT_EXP
    return _CURRENCY_EXP.get(currency.strip().upper(), DEFAULT_EXP)


def to_minor(value: "Decimal | float | int | str", exp: int) -> int:
    """Convert a decimal value to integer minor units.

    Args:
        value: Human-readable decimal (from LLM output or user input).
               Must be a non-negative number.
        exp:   Exponent — number of decimal places to preserve.
               price_minor = value × 10^exp

    Returns:
        Integer in minor units, rounded half-up.

    Raises:
        ValueError: If value is negative, non-numeric, or exp is negative.

    Examples:
        to_minor(2.50, 2)  → 250
        to_minor("1.5", 3) → 1500
        to_minor(500, 0)   → 500
    """
    if exp < 0:
        raise ValueError(f"exp must be >= 0, got {exp}")

    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"Cannot convert {value!r} to Decimal")

    if d < 0:
        raise ValueError(f"Value must be non-negative, got {d}")

    factor = Decimal(10**exp)
    return int((d * factor).to_integral_value(rounding=ROUND_HALF_UP))


def to_display(minor: int, exp: int) -> Decimal:
    """Convert integer minor units back to a human-readable Decimal.

    Args:
        minor: Stored integer (price_minor or unit_qty_minor).
        exp:   Exponent matching what was used in to_minor().

    Returns:
        Decimal with exp decimal places preserved.

    Examples:
        to_display(250,  2) → Decimal('2.50')
        to_display(1500, 3) → Decimal('1.500')
        to_display(500,  0) → Decimal('500')
    """
    if exp < 0:
        raise ValueError(f"exp must be >= 0, got {exp}")

    result = Decimal(minor) / Decimal(10**exp)
    # Quantize to preserve trailing zeros for display fidelity.
    quantizer = Decimal(10) ** -exp
    return result.quantize(quantizer)


def format_price(minor: int, exp: int, currency: str | None = None) -> str:
    """Format a stored price as a human-readable string.

    Examples:
        format_price(250, 2, "SGD")  → "2.50 SGD"
        format_price(500, 0, "JPY")  → "500 JPY"
        format_price(1500, 3)        → "1.500"
    """
    display = to_display(minor, exp)
    if currency:
        return f"{display} {currency.upper()}"
    return str(display)


def format_qty(minor: int, exp: int, unit: str | None = None) -> str:
    """Format a stored unit quantity as a human-readable string.

    Examples:
        format_qty(1500, 3, "kg")  → "1.500 kg"
        format_qty(1000, 3, "ltr") → "1.000 ltr"
        format_qty(5000, 3)        → "5.000"
    """
    display = to_display(minor, exp)
    if unit:
        return f"{display} {unit}"
    return str(display)
