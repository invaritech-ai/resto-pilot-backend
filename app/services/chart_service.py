"""Chart generation for Telegram bot responses.

Uses seaborn + matplotlib rendered to PNG bytes (no display required).
All functions return raw bytes suitable for send_photo().
"""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.inventory_balances import InventoryBalance
    from app.db.models.inventory_items import InventoryItem

_MAX_ITEMS = 20  # cap total items across all panels


def _unit_group(unit: str | None) -> str:
    """Canonical group label for a unit string.

    Weight subunits (g) are kept separate from kg so the caller can decide
    whether to normalise.  Everything unrecognised keeps its original label
    so unusual units (pkt, tray, box …) each form their own panel.
    """
    if not unit:
        return "count"
    u = unit.lower().strip()
    if u in ("kg", "kgs", "kilogram", "kilograms"):
        return "kg"
    if u in ("g", "gram", "grams"):
        return "g"
    if u in ("l", "liter", "liters", "litre", "litres"):
        return "L"
    if u in ("ml", "milliliter", "milliliters", "millilitre", "millilitres"):
        return "ml"
    return unit  # pkt, tray, pk, dozen, pcs, … kept verbatim


def _bar_color(v: float) -> str:
    if v < 0:
        return "#e74c3c"   # red  — negative stock
    if v == 0:
        return "#f39c12"   # amber — zero stock
    return "#1abc9c"       # teal  — positive


def make_stock_chart(
    items_with_balances: list[tuple["InventoryItem", "InventoryBalance | None"]],
    restaurant_name: str,
) -> bytes:
    """Per-unit-group bar chart of current stock levels.

    Items are separated into panels by unit so that kg items are only compared
    to other kg items, pkt items to pkt items, etc.  Mixing units on a single
    axis would produce a misleading scale (e.g. 300 g >> 4 kg visually).

    Returns PNG bytes.
    """
    import matplotlib
    matplotlib.use("Agg")  # headless — no display required
    import matplotlib.pyplot as plt
    import seaborn as sns

    # --- group items by unit ---
    # OrderedDict-style: group_label → [(item_name, balance_float, display_unit)]
    groups: dict[str, list[tuple[str, float, str]]] = {}
    for item, balance in items_with_balances[:_MAX_ITEMS]:
        bal = float(balance.balance) if balance is not None else 0.0
        group = _unit_group(item.unit)
        display_unit = item.unit or ""
        groups.setdefault(group, []).append((item.name, bal, display_unit))

    if not groups:
        return b""

    # Sort groups: weight first, then volume, then named units, then count
    _GROUP_ORDER = ["kg", "g", "L", "ml"]
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: (_GROUP_ORDER.index(kv[0]) if kv[0] in _GROUP_ORDER else 99, kv[0]),
    )

    n_panels = len(sorted_groups)
    panel_sizes = [len(items) for _, items in sorted_groups]
    total_rows = sum(panel_sizes)

    sns.set_theme(style="whitegrid", font_scale=0.9)

    fig_height = max(5, total_rows * 0.52 + n_panels * 1.2)
    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(9, fig_height),
        gridspec_kw={"height_ratios": panel_sizes} if n_panels > 1 else None,
        squeeze=False,
    )

    for ax, (group_label, items) in zip(axes[:, 0], sorted_groups):
        # Reverse so the first item in the list appears at the top
        names = [name for name, _, _ in reversed(items)]
        balances = [bal for _, bal, _ in reversed(items)]
        units = [u for _, _, u in reversed(items)]
        colors = [_bar_color(b) for b in balances]

        bars = ax.barh(names, balances, color=colors, edgecolor="none", height=0.6)

        # Annotate each bar with value + unit
        x_range = (max(balances) - min(balances)) or max(abs(b) for b in balances) or 1
        offset = 0.015 * x_range
        for bar, val, unit in zip(bars, balances, units):
            label = f"{val:g}{(' ' + unit) if unit else ''}"
            x_pos = bar.get_width()
            ha = "left" if x_pos >= 0 else "right"
            ax.text(
                x_pos + (offset if x_pos >= 0 else -offset),
                bar.get_y() + bar.get_height() / 2,
                label,
                va="center",
                ha=ha,
                fontsize=8,
                color="#2c3e50",
            )

        ax.axvline(0, color="#bdc3c7", linewidth=0.8, linestyle="--")
        ax.set_xlabel(f"Balance ({group_label})", labelpad=6, fontsize=9)
        ax.set_title(group_label, loc="left", fontsize=9, color="#7f8c8d", pad=4)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="y", labelsize=8.5)

    fig.suptitle(f"{restaurant_name} — Stock Levels", fontweight="bold", y=1.01, fontsize=11)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
