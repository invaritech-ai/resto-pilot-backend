"""Message formatters for Telegram.

Handles integer→display conversion for prices and quantities.
All prices are stored as integer minor units (e.g., cents).
Display converts back to human-readable decimals.

Example:
    price_minor=250, price_exp=2 → "2.50"
    unit_qty_minor=1500, unit_qty_exp=3 → "1.500"
"""

from __future__ import annotations

import uuid
from typing import Any

from app.services.money import to_display


def format_price(price_minor: int, price_exp: int, currency: str | None = None) -> str:
    """Format price from minor units to display string.

    Args:
        price_minor: Price in minor units (e.g., cents)
        price_exp: Exponent (divide by 10^exp)
        currency: Optional currency code (e.g., "SGD")

    Returns:
        Formatted price string (e.g., "2.50 SGD")
    """
    display = str(to_display(price_minor, price_exp))
    if currency:
        return f"{display} {currency}"
    return display


def format_quantity(
    qty_minor: int | None, qty_exp: int | None, unit: str | None
) -> str:
    """Format quantity from minor units to display string.

    Args:
        qty_minor: Quantity in minor units
        qty_exp: Exponent (divide by 10^exp)
        unit: Unit string (e.g., "kg", "case")

    Returns:
        Formatted quantity string (e.g., "1.500 kg")
    """
    if qty_minor is None:
        return unit or ""

    display = str(to_display(qty_minor, qty_exp or 0))
    if unit:
        return f"{display} {unit}"
    return display


def format_price_item(
    idx: int,
    item_name: str,
    price_minor: int,
    price_exp: int,
    currency: str | None,
    unit: str | None = None,
    unit_qty_minor: int | None = None,
    unit_qty_exp: int | None = None,
) -> str:
    """Format a single price item for display.

    Args:
        idx: Item index (1-based)
        item_name: Name of the item
        price_minor: Price in minor units
        price_exp: Price exponent
        currency: Currency code
        unit: Unit string
        unit_qty_minor: Unit quantity in minor units
        unit_qty_exp: Unit quantity exponent

    Returns:
        Formatted line like "1. Tomato — 2.50 SGD/kg"
    """
    price_str = format_price(price_minor, price_exp, currency)

    if unit and unit_qty_minor is not None:
        qty_str = format_quantity(unit_qty_minor, unit_qty_exp, unit)
        return f"{idx}. {item_name} — {price_str}/{qty_str}"
    elif unit:
        return f"{idx}. {item_name} — {price_str}/{unit}"
    else:
        return f"{idx}. {item_name} — {price_str}"


def render_upload_review(
    staging_id: uuid.UUID,
    supplier_name: str | None,
    effective_date: str | None,
    currency: str | None,
    items: list[dict[str, Any]],
) -> str:
    """Render upload review message.

    Args:
        staging_id: Staging record ID
        supplier_name: Parsed supplier name
        effective_date: Price list effective date
        currency: Currency code
        items: List of item dicts with fields:
            - item_name: str
            - price_minor: int
            - price_exp: int
            - unit: str | None
            - unit_qty_minor: int | None
            - unit_qty_exp: int | None

    Returns:
        Formatted message text
    """
    lines = [
        "📄 **Price List Review**\n",
        f"Supplier: {supplier_name or 'Unknown'}",
        f"Date: {effective_date or 'Not found'}",
        f"Currency: {currency or 'Not found'}",
        f"Items: {len(items)}\n",
    ]

    for i, item in enumerate(items, 1):
        line = format_price_item(
            idx=i,
            item_name=item.get("item_name", "Unknown"),
            price_minor=item.get("price_minor", 0),
            price_exp=item.get("price_exp", 0),
            currency=currency,
            unit=item.get("unit"),
            unit_qty_minor=item.get("unit_qty_minor"),
            unit_qty_exp=item.get("unit_qty_exp"),
        )
        lines.append(line)

    return "\n".join(lines)


def render_supplier_list(
    suppliers: list[Any],
    offset: int = 0,
) -> str:
    """Render supplier list message.

    Args:
        suppliers: List of Supplier ORM objects
        offset: Pagination offset

    Returns:
        Formatted message text
    """
    if not suppliers:
        return "No suppliers found."

    lines = ["📋 **Suppliers**\n"]

    for i, s in enumerate(suppliers, 1):
        lines.append(f"{offset + i}. {s.name}")

    return "\n".join(lines)


def render_success(message: str) -> str:
    """Render success message with checkmark."""
    return f"✅ {message}"


def render_error(message: str) -> str:
    """Render error message."""
    return f"❌ {message}"
