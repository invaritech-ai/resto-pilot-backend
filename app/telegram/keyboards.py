"""Inline keyboard builders for Telegram messages.

Wire format: `action:id:param` (Telegram 64-byte limit)
UUID as 32-char hex, no dashes.
"""

from __future__ import annotations

import uuid
from typing import Any

# Telegram inline keyboard button types
InlineKeyboardButton = dict[str, Any]
InlineKeyboardMarkup = dict[str, list[list[InlineKeyboardButton]]]


def uuid_to_hex(id: uuid.UUID) -> str:
    """Convert UUID to 32-char hex string (no dashes)."""
    return id.hex


def hex_to_uuid(hex_str: str) -> uuid.UUID:
    """Convert 32-char hex string back to UUID."""
    return uuid.UUID(hex=hex_str)


def make_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """Create a single inline keyboard button."""
    return {"text": text, "callback_data": callback_data}


# ---------------------------------------------------------------------------
# Callback data builders
# ---------------------------------------------------------------------------


def cb_review_upload(staging_id: uuid.UUID) -> str:
    """Review upload callback: rev_u:{staging_hex}"""
    return f"rev_u:{uuid_to_hex(staging_id)}"


def cb_confirm_upload(staging_id: uuid.UUID) -> str:
    """Confirm upload callback: conf_u:{staging_hex}"""
    return f"conf_u:{uuid_to_hex(staging_id)}"


def cb_delete_upload(staging_id: uuid.UUID) -> str:
    """Delete upload callback: del_u:{staging_hex}"""
    return f"del_u:{uuid_to_hex(staging_id)}"


def cb_edit_row(staging_id: uuid.UUID, idx: int) -> str:
    """Edit row callback: ed_row:{staging_hex}:{idx}"""
    return f"ed_row:{uuid_to_hex(staging_id)}:{idx}"


def cb_list_page(list_type: str, page: int) -> str:
    """Pagination callback: list_p:{type}:{page}"""
    return f"list_p:{list_type}:{page}"


def cb_resolve_handshake(handshake_id: uuid.UUID, answer: str) -> str:
    """Resolve handshake callback: res_h:{handshake_hex}:{answer}"""
    return f"res_h:{uuid_to_hex(handshake_id)}:{answer}"


def cb_set_supplier(staging_id: uuid.UUID, supplier_id: uuid.UUID) -> str:
    """Set supplier callback: set_sup:{staging_hex}:{supplier_hex}"""
    return f"set_sup:{uuid_to_hex(staging_id)}:{uuid_to_hex(supplier_id)}"


def cb_new_supplier(staging_id: uuid.UUID) -> str:
    """Create new supplier callback: new_sup:{staging_hex}"""
    return f"new_sup:{uuid_to_hex(staging_id)}"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def upload_review_keyboard(
    staging_id: uuid.UUID,
    item_count: int,
    show_edit_buttons: bool = True,
) -> InlineKeyboardMarkup:
    """Build keyboard for upload review message.

    Layout:
        [ ✅ Confirm All ]  [ ❌ Cancel ]
        [ ✏️ Edit Item #1 ] [ ✏️ Edit Item #2 ] ...
    """
    row1 = [
        make_button("✅ Confirm All", cb_confirm_upload(staging_id)),
        make_button("❌ Cancel", cb_delete_upload(staging_id)),
    ]

    keyboard = [row1]

    if show_edit_buttons and item_count > 0:
        # Add edit buttons (max 4 per row to stay within limits)
        edit_buttons = [
            make_button(f"✏️ #{i + 1}", cb_edit_row(staging_id, i))
            for i in range(min(item_count, 8))  # Max 8 edit buttons
        ]
        # Split into rows of 4
        for i in range(0, len(edit_buttons), 4):
            keyboard.append(edit_buttons[i : i + 4])

    return {"inline_keyboard": keyboard}


def pagination_keyboard(
    list_type: str,
    current_page: int,
    has_next: bool,
    has_prev: bool,
) -> InlineKeyboardMarkup:
    """Build pagination keyboard.

    Layout:
        [ ← Prev ]  [ Next → ]
    """
    row = []
    if has_prev:
        row.append(make_button("← Prev", cb_list_page(list_type, current_page - 1)))
    if has_next:
        row.append(make_button("Next →", cb_list_page(list_type, current_page + 1)))

    if not row:
        return {"inline_keyboard": []}

    return {"inline_keyboard": [row]}


def supplier_selection_keyboard(
    staging_id: uuid.UUID,
    suppliers: list[tuple[uuid.UUID, str]],
) -> InlineKeyboardMarkup:
    """Build supplier selection keyboard for linking.

    Layout:
        [ ABC Wholesalers ]
        [ XYZ Supplies ]
        [ ✨ Create New ]
    """
    keyboard = []

    for supplier_id, name in suppliers[:5]:  # Max 5 suppliers
        keyboard.append([make_button(name, cb_set_supplier(staging_id, supplier_id))])

    keyboard.append([make_button("✨ Create New", cb_new_supplier(staging_id))])

    return {"inline_keyboard": keyboard}


def handshake_keyboard(
    handshake_id: uuid.UUID,
    options: list[str] | None = None,
) -> InlineKeyboardMarkup:
    """Build handshake resolution keyboard.

    Default options: ["yes", "no"]

    Layout:
        [ ✅ Yes ]  [ ❌ No ]
    """
    if options is None:
        options = ["yes", "no"]

    row = []
    for opt in options:
        emoji = "✅" if opt.lower() == "yes" else "❌" if opt.lower() == "no" else "➡️"
        row.append(
            make_button(
                f"{emoji} {opt.title()}",
                cb_resolve_handshake(handshake_id, opt.lower()),
            )
        )

    return {"inline_keyboard": [row]}
