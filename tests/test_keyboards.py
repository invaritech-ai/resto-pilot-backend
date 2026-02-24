"""Tests for app/telegram/keyboards.py — PO callback byte sizes and keyboard shapes."""

from __future__ import annotations

import uuid

import pytest

from app.telegram.keyboards import (
    cb_po_add_item,
    cb_po_cancel,
    cb_po_receive,
    cb_po_submit,
    cb_po_view,
    cb_reorder_to_po,
    po_draft_keyboard,
    po_sent_keyboard,
    reorder_keyboard,
)

_MAX = 64
_PO_ID = uuid.UUID("12345678123412341234123456789abc")
_SUP_ID = uuid.UUID("abcdef01abcdef01abcdef01abcdef01")


# ---------------------------------------------------------------------------
# Callback byte-length assertions
# ---------------------------------------------------------------------------


class TestCallbackSizes:
    def test_cb_po_submit_within_limit(self):
        cb = cb_po_submit(_PO_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("po_sub:")

    def test_cb_po_receive_within_limit(self):
        cb = cb_po_receive(_PO_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("po_rcv:")

    def test_cb_po_cancel_within_limit(self):
        cb = cb_po_cancel(_PO_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("po_can:")

    def test_cb_po_add_item_within_limit(self):
        cb = cb_po_add_item(_PO_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("po_add:")

    def test_cb_po_view_within_limit(self):
        cb = cb_po_view(_PO_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("po_view:")

    def test_cb_reorder_to_po_within_limit(self):
        cb = cb_reorder_to_po(_SUP_ID)
        assert len(cb.encode("utf-8")) <= _MAX
        assert cb.startswith("reorder_po:")

    def test_all_callbacks_contain_hex(self):
        """Each callback encodes the UUID as 32-char hex without dashes."""
        hex_id = _PO_ID.hex
        assert hex_id in cb_po_submit(_PO_ID)
        assert hex_id in cb_po_receive(_PO_ID)
        assert hex_id in cb_po_cancel(_PO_ID)
        assert hex_id in cb_po_add_item(_PO_ID)
        assert hex_id in cb_po_view(_PO_ID)


# ---------------------------------------------------------------------------
# Keyboard shape tests
# ---------------------------------------------------------------------------


class TestPoDraftKeyboard:
    def test_has_inline_keyboard(self):
        kb = po_draft_keyboard(_PO_ID)
        assert "inline_keyboard" in kb

    def test_has_two_rows(self):
        kb = po_draft_keyboard(_PO_ID)
        assert len(kb["inline_keyboard"]) == 2

    def test_first_row_is_add_item(self):
        kb = po_draft_keyboard(_PO_ID)
        btn = kb["inline_keyboard"][0][0]
        assert "Add item" in btn["text"]
        assert btn["callback_data"] == cb_po_add_item(_PO_ID)

    def test_second_row_submit_and_cancel(self):
        kb = po_draft_keyboard(_PO_ID)
        row = kb["inline_keyboard"][1]
        texts = [b["text"] for b in row]
        assert any("Submit" in t for t in texts)
        assert any("Cancel" in t for t in texts)


class TestPoSentKeyboard:
    def test_has_one_row(self):
        kb = po_sent_keyboard(_PO_ID)
        assert len(kb["inline_keyboard"]) == 1

    def test_row_has_receive_and_cancel(self):
        kb = po_sent_keyboard(_PO_ID)
        row = kb["inline_keyboard"][0]
        texts = [b["text"] for b in row]
        assert any("received" in t.lower() for t in texts)
        assert any("Cancel" in t for t in texts)


class TestReorderKeyboard:
    def test_empty_returns_empty_keyboard(self):
        kb = reorder_keyboard([])
        assert kb == {"inline_keyboard": []}

    def test_one_supplier_one_button(self):
        kb = reorder_keyboard([(_SUP_ID, "Cheong Hing")])
        assert len(kb["inline_keyboard"]) == 1
        btn = kb["inline_keyboard"][0][0]
        assert "Cheong Hing" in btn["text"]
        assert btn["callback_data"] == cb_reorder_to_po(_SUP_ID)

    def test_deduplicates_same_supplier(self):
        pairs = [(_SUP_ID, "Cheong Hing"), (_SUP_ID, "Cheong Hing")]
        kb = reorder_keyboard(pairs)
        assert len(kb["inline_keyboard"]) == 1

    def test_caps_at_five_suppliers(self):
        pairs = [(uuid.uuid4(), f"Supplier {i}") for i in range(8)]
        kb = reorder_keyboard(pairs)
        assert len(kb["inline_keyboard"]) == 5
