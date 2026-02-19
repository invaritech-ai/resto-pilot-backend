"""Deterministic regex parser for free-text stock phrases.

Supported patterns:
  use/used {qty}{unit?} {item}       → kind="use"       (debit transaction)
  {qty}{unit?} {item} left/remaining → kind="reconcile"  (set balance to target)

Returns StockPhrase or None (no match).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

UNIT_ALIASES: dict[str, str] = {
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gram": "g", "grams": "g",
    "l": "L", "liter": "L", "liters": "L", "litre": "L", "litres": "L",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs",
    "unit": "pcs", "units": "pcs",
    "dozen": "dozen", "doz": "dozen",
}

# "used 1kg onion" / "use 1.5 kg chicken breast"
_USED_RE = re.compile(
    r"^(?:used?|use)\s+(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)?\s+(.+?)\s*$",
    re.IGNORECASE,
)
# "1kg onion left" / "1.5 kg chicken breast remaining"
_LEFT_RE = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)?\s+(.+?)\s+(?:left|remaining|rem)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StockPhrase:
    item_name: str
    qty: Decimal
    unit: str | None
    kind: Literal["use", "reconcile"]


def _parse_qty(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def _resolve_unit(candidate: str | None) -> tuple[str | None, str]:
    """Return (unit, leftover).

    If *candidate* is a recognised unit alias, return (canonical_unit, "").
    Otherwise return (None, candidate) so the caller can prepend it to the item name.
    """
    if not candidate:
        return None, ""
    lower = candidate.lower()
    if lower in UNIT_ALIASES:
        return UNIT_ALIASES[lower], ""
    return None, candidate


def parse_stock_phrase(text: str) -> StockPhrase | None:
    """Parse a free-text stock phrase.

    Returns a :class:`StockPhrase` on success, ``None`` if the text does not
    match either supported pattern.
    """
    text = text.strip()
    for pattern, kind in ((_USED_RE, "use"), (_LEFT_RE, "reconcile")):
        m = pattern.match(text)
        if not m:
            continue
        qty_raw, unit_candidate, item_raw = (
            m.group(1),
            m.group(2),
            m.group(3).strip(),
        )
        qty = _parse_qty(qty_raw)
        if qty is None or qty <= 0:
            continue
        unit, leftover = _resolve_unit(unit_candidate)
        item_name = (f"{leftover} {item_raw}".strip() if leftover else item_raw).strip()
        if not item_name or item_name.lower() in UNIT_ALIASES:
            continue
        return StockPhrase(item_name=item_name, qty=qty, unit=unit, kind=kind)
    return None
