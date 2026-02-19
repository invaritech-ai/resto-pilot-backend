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

_MAX_ITEMS = 15  # cap chart at 15 bars to keep it readable


def make_stock_chart(
    items_with_balances: list[tuple["InventoryItem", "InventoryBalance | None"]],
    restaurant_name: str,
) -> bytes:
    """Horizontal bar chart of current stock levels.

    - Negative balances rendered in red, zero in amber, positive in teal.
    - Returns PNG bytes.
    """
    import matplotlib
    matplotlib.use("Agg")  # headless — no display required
    import matplotlib.pyplot as plt
    import seaborn as sns

    # Trim to most recent _MAX_ITEMS and reverse so highest bar is on top
    rows = items_with_balances[:_MAX_ITEMS]
    names = [item.name for item, _ in reversed(rows)]
    balances = [
        float(bal.balance) if bal is not None else 0.0
        for _, bal in reversed(rows)
    ]
    units = [
        item.unit or "" for item, _ in reversed(rows)
    ]

    def _bar_color(v: float) -> str:
        if v < 0:
            return "#e74c3c"   # red
        if v == 0:
            return "#f39c12"   # amber
        return "#1abc9c"       # teal

    colors = [_bar_color(b) for b in balances]

    fig_height = max(4, len(names) * 0.55 + 1.5)
    sns.set_theme(style="whitegrid", font_scale=0.95)
    fig, ax = plt.subplots(figsize=(9, fig_height))

    bars = ax.barh(names, balances, color=colors, edgecolor="none", height=0.6)

    # Annotate each bar with its value + unit
    for bar, val, unit in zip(bars, balances, units):
        label = f"{val:g}{(' ' + unit) if unit else ''}"
        x_pos = bar.get_width()
        ha = "left" if x_pos >= 0 else "right"
        offset = 0.02 * (max(balances) - min(balances) or 1)
        ax.text(
            x_pos + (offset if x_pos >= 0 else -offset),
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            ha=ha,
            fontsize=8.5,
            color="#2c3e50",
        )

    ax.axvline(0, color="#bdc3c7", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Balance", labelpad=8)
    ax.set_title(f"{restaurant_name} — Stock Levels", pad=12, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
