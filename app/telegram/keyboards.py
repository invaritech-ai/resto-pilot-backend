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
_MAX_CALLBACK_BYTES = 64


def uuid_to_hex(id: uuid.UUID) -> str:
    """Convert UUID to 32-char hex string (no dashes)."""
    return id.hex


def hex_to_uuid(hex_str: str) -> uuid.UUID:
    """Convert 32-char hex string back to UUID."""
    return uuid.UUID(hex=hex_str)


def make_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """Create a single inline keyboard button."""
    return {"text": text, "callback_data": callback_data}


def _ensure_callback_limit(callback_data: str) -> str:
    """Guard Telegram callback_data hard limit."""
    byte_len = len(callback_data.encode("utf-8"))
    if byte_len > _MAX_CALLBACK_BYTES:
        raise ValueError(
            f"callback_data exceeds Telegram 64-byte limit: {byte_len} bytes ({callback_data})"
        )
    return callback_data


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


def cb_set_supplier(staging_id: uuid.UUID, supplier_ref: int | str) -> str:
    """Set supplier callback: set_sup:{staging_hex}:{supplier_ref}.

    supplier_ref is a short candidate index (preferred) to keep callback payload
    below Telegram's 64-byte limit.
    """
    return _ensure_callback_limit(f"set_sup:{uuid_to_hex(staging_id)}:{supplier_ref}")


def cb_new_supplier(staging_id: uuid.UUID) -> str:
    """Create new supplier callback: new_sup:{staging_hex}"""
    return f"new_sup:{uuid_to_hex(staging_id)}"


def cb_type_supplier(staging_id: uuid.UUID) -> str:
    """Type supplier name callback: type_sup:{staging_hex}"""
    return _ensure_callback_limit(f"type_sup:{uuid_to_hex(staging_id)}")


def cb_rev_page(staging_id: uuid.UUID, page: int) -> str:
    """Review page navigation: rev_p:{staging_hex}:{page}"""
    return _ensure_callback_limit(f"rev_p:{uuid_to_hex(staging_id)}:{page}")


def cb_doc_type(staging_id: uuid.UUID, doc_type: str) -> str:
    """Document type selection: doc_type:{staging_hex}:{doc_type}"""
    return f"doc_type:{uuid_to_hex(staging_id)}:{doc_type}"


def cb_use_match(staging_id: uuid.UUID, idx: int, match_ref: int | str) -> str:
    """Accept fuzzy match for line item: use_match:{staging_hex}:{idx}:{match_ref}.

    match_ref is a short candidate index (preferred) to keep callback payload
    below Telegram's 64-byte limit.
    """
    return _ensure_callback_limit(
        f"use_match:{uuid_to_hex(staging_id)}:{idx}:{match_ref}"
    )


def cb_mk_item(staging_id: uuid.UUID, idx: int) -> str:
    """Create new inventory item for line item: mk_item:{staging_hex}:{idx}"""
    return f"mk_item:{uuid_to_hex(staging_id)}:{idx}"


def cb_skip_item(staging_id: uuid.UUID, idx: int) -> str:
    """Skip line item (don't add to inventory): skip_item:{staging_hex}:{idx}"""
    return f"skip_item:{uuid_to_hex(staging_id)}:{idx}"


def cb_edit_field(staging_id: uuid.UUID, idx: int, field: str) -> str:
    """Edit a specific field of a line item: ed_fld:{staging_hex}:{idx}:{field}"""
    return _ensure_callback_limit(f"ed_fld:{uuid_to_hex(staging_id)}:{idx}:{field}")


def cb_open_upload(staging_id: uuid.UUID) -> str:
    """Re-open a pending_review staging record: open_u:{staging_hex}"""
    return _ensure_callback_limit(f"open_u:{uuid_to_hex(staging_id)}")


def cb_pick_currency(staging_id: uuid.UUID) -> str:
    """Show currency picker for a staging record: pick_cur:{staging_hex}"""
    return _ensure_callback_limit(f"pick_cur:{uuid_to_hex(staging_id)}")


def cb_set_currency(staging_id: uuid.UUID, code: str) -> str:
    """Set currency on a staging record: set_cur:{staging_hex}:{code}"""
    return _ensure_callback_limit(f"set_cur:{uuid_to_hex(staging_id)}:{code}")


def cb_stock_conf(direction: str) -> str:
    """Confirm quick stock adjustment: stock_conf:{in|out}"""
    return f"stock_conf:{direction}"


def cb_stock_new(direction: str) -> str:
    """Create new item + record quick stock adjustment: stock_new:{in|out}"""
    return f"stock_new:{direction}"


def cb_stock_cancel() -> str:
    """Cancel pending quick stock adjustment: stock_cancel"""
    return "stock_cancel"


def cb_quick_adj(item_id: uuid.UUID, direction: str) -> str:
    """Quick ±1 inventory adjustment callback: qadj:{item_hex}:{in|out}"""
    return f"qadj:{uuid_to_hex(item_id)}:{direction}"


def quick_adj_rows(items: list) -> list:
    """Build [#N +] [#N -] button rows for the current inventory page.

    Groups 4 buttons per row: [#1 +] [#1 -] [#2 +] [#2 -]
    """
    rows: list = []
    buttons: list = []
    for i, (item, _) in enumerate(items, 1):
        buttons.append(make_button(f"#{i} +", cb_quick_adj(item.id, "in")))
        buttons.append(make_button(f"#{i} -", cb_quick_adj(item.id, "out")))
        if len(buttons) == 4:
            rows.append(buttons)
            buttons = []
    if buttons:
        rows.append(buttons)
    return rows


def stock_adj_keyboard(
    direction: str,
    match_score: float,
    item_found: bool,
) -> "InlineKeyboardMarkup":
    """Confirm keyboard for a pending quick stock adjustment.

    - High confidence (≥0.8): single Confirm + Cancel row.
    - Medium confidence (0.45–0.8): Confirm + Create-new + Cancel.
    - No match: Add-new + Cancel.
    """
    if item_found and match_score >= 0.8:
        return {"inline_keyboard": [[
            make_button("✅ Confirm", cb_stock_conf(direction)),
            make_button("✗ Cancel", cb_stock_cancel()),
        ]]}
    elif item_found:
        return {"inline_keyboard": [[
            make_button("✅ Yes, use this", cb_stock_conf(direction)),
            make_button("➕ Create new item", cb_stock_new(direction)),
            make_button("✗ Cancel", cb_stock_cancel()),
        ]]}
    else:
        return {"inline_keyboard": [[
            make_button("➕ Add & record", cb_stock_new(direction)),
            make_button("✗ Cancel", cb_stock_cancel()),
        ]]}


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

    for rank, (_supplier_id, name) in enumerate(suppliers[:5]):  # Max 5 suppliers
        keyboard.append([make_button(name, cb_set_supplier(staging_id, rank))])

    keyboard.append([make_button("✨ Create New", cb_new_supplier(staging_id))])

    return {"inline_keyboard": keyboard}


def doc_type_keyboard(staging_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Ask user what type of document they uploaded.

    Layout:
        [ 1️⃣ Invoice ]  [ 2️⃣ Price List ]
    """
    return {
        "inline_keyboard": [
            [
                make_button("1️⃣ Invoice", cb_doc_type(staging_id, "invoice")),
                make_button("2️⃣ Price List", cb_doc_type(staging_id, "price_list")),
            ]
        ]
    }


def cb_po_submit(po_id: uuid.UUID) -> str:
    """Submit draft PO callback: po_sub:{hex}  (39 bytes)"""
    return _ensure_callback_limit(f"po_sub:{uuid_to_hex(po_id)}")


def cb_po_receive(po_id: uuid.UUID) -> str:
    """Mark PO received callback: po_rcv:{hex}  (39 bytes)"""
    return _ensure_callback_limit(f"po_rcv:{uuid_to_hex(po_id)}")


def cb_po_cancel(po_id: uuid.UUID) -> str:
    """Cancel PO callback: po_can:{hex}  (39 bytes)"""
    return _ensure_callback_limit(f"po_can:{uuid_to_hex(po_id)}")


def cb_po_add_item(po_id: uuid.UUID) -> str:
    """Enter add-item text mode for PO: po_add:{hex}  (39 bytes)"""
    return _ensure_callback_limit(f"po_add:{uuid_to_hex(po_id)}")


def cb_po_view(po_id: uuid.UUID) -> str:
    """Re-render PO detail: po_view:{hex}  (40 bytes)"""
    return _ensure_callback_limit(f"po_view:{uuid_to_hex(po_id)}")


def cb_reorder_to_po(supplier_id: uuid.UUID) -> str:
    """Create draft PO from reorder suggestions: reorder_po:{hex}  (43 bytes)"""
    return _ensure_callback_limit(f"reorder_po:{uuid_to_hex(supplier_id)}")


def po_draft_keyboard(po_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Keyboard shown on a draft PO.

    Layout:
        [ ➕ Add item ]
        [ ✅ Submit order ]  [ ✗ Cancel ]
    """
    return {
        "inline_keyboard": [
            [make_button("➕ Add item", cb_po_add_item(po_id))],
            [
                make_button("✅ Submit order", cb_po_submit(po_id)),
                make_button("✗ Cancel", cb_po_cancel(po_id)),
            ],
        ]
    }


def po_sent_keyboard(po_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Keyboard shown on a submitted (sent) PO.

    Layout:
        [ ✅ Mark received ]  [ ✗ Cancel order ]
    """
    return {
        "inline_keyboard": [
            [
                make_button("✅ Mark received", cb_po_receive(po_id)),
                make_button("✗ Cancel order", cb_po_cancel(po_id)),
            ]
        ]
    }


def reorder_keyboard(
    supplier_ids_names: list[tuple[uuid.UUID, str]],
) -> InlineKeyboardMarkup:
    """Keyboard shown with /reorder output.

    One [Order from <Supplier>] button per unique supplier, capped at 5.

    Layout:
        [ Order from Cheong Hing ]
        [ Order from Metro Fresh ]
        ...
    """
    keyboard = []
    seen: set[uuid.UUID] = set()
    for supplier_id, supplier_name in supplier_ids_names:
        if supplier_id in seen or len(keyboard) >= 5:
            continue
        seen.add(supplier_id)
        label = f"Order from {supplier_name}"
        keyboard.append([make_button(label, cb_reorder_to_po(supplier_id))])
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
