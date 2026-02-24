"""Tests for PO button handlers (Phase 2 — Smart Procurement)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.telegram.handlers.buttons import handle

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

RESTAURANT_ID = uuid.uuid4()
SUPPLIER_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = USER_ID
    user.chat_id = 100
    user.context = {"active_restaurant_id": str(RESTAURANT_ID)}
    return user


def _make_ctx_svc() -> MagicMock:
    ctx_svc = MagicMock()
    ctx_svc.get_active_restaurant_id.return_value = RESTAURANT_ID
    ctx_svc.get_fields.return_value = {}
    return ctx_svc


def _make_db() -> MagicMock:
    db = MagicMock()
    db.commit = MagicMock()
    return db


def _make_settings() -> MagicMock:
    return MagicMock()


def _make_po(
    status: str = "draft",
    po_id: uuid.UUID | None = None,
    supplier_name: str = "Cheong Hing",
) -> MagicMock:
    po = MagicMock()
    po.id = po_id or PO_ID
    po.restaurant_id = RESTAURANT_ID
    po.supplier_id = SUPPLIER_ID
    po.supplier_name = supplier_name
    po.status = status
    po.sent_at = None
    po.received_at = None
    po.created_at = MagicMock()
    po.created_at.strftime.return_value = "1 Feb 2026"
    return po


def _make_update(action: str, hex_id: str | None = None) -> dict:
    """Build a callback_query update."""
    parts = [action]
    if hex_id:
        parts.append(hex_id)
    return {
        "callback_query": {
            "id": "cb123",
            "data": ":".join(parts),
            "message": {
                "message_id": 42,
                "chat": {"id": 100},
            },
            "from": {"id": 999},
        }
    }


def _po_hex(po_id: uuid.UUID = PO_ID) -> str:
    return po_id.hex


def _sup_hex(supplier_id: uuid.UUID = SUPPLIER_ID) -> str:
    return supplier_id.hex


# ---------------------------------------------------------------------------
# Auth helper: _authorize_po_callback
# ---------------------------------------------------------------------------

class TestAuthorizePOCallback:
    def test_denies_non_member(self):
        """Membership check fails → answer_callback_query called with alert."""
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="draft")
        db.get.return_value = po

        update = _make_update("po_sub", _po_hex())

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(False, False)),
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
        ):
            handle(update, user, db, ctx_svc, _make_settings())

        # Should deny — answer_callback_query called with show_alert=True
        mock_ack.assert_called()
        ack_kwargs = mock_ack.call_args[1]
        assert ack_kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# _handle_po_submit
# ---------------------------------------------------------------------------

class TestHandlePoSubmit:
    def test_transitions_draft_to_sent(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="draft")
        db.get.return_value = po

        update = _make_update("po_sub", _po_hex())

        from app.services.purchase_order_service import PurchaseOrderService

        submitted_po = _make_po(status="sent")
        submitted_po.sent_at = MagicMock()
        submitted_po.sent_at.strftime.return_value = "1 Feb 2026"

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
            patch("app.telegram.handlers.buttons.edit_message_text"),
            patch("app.telegram.handlers.buttons.send_message_with_keyboard"),
        ):
            MockPOSvc.return_value.submit.return_value = submitted_po
            db.scalars.return_value.all.return_value = []
            handle(update, user, db, ctx_svc, _make_settings())

        db.commit.assert_called()
        mock_ack.assert_called()
        ack_text = mock_ack.call_args[1]["text"]
        assert "submitted" in ack_text.lower()

    def test_already_sent_shows_error(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="sent")
        db.get.return_value = po

        update = _make_update("po_sub", _po_hex())

        from app.services.purchase_order_service import POStatusError

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
        ):
            MockPOSvc.return_value.submit.side_effect = POStatusError("Already sent")
            handle(update, user, db, ctx_svc, _make_settings())

        ack_kwargs = mock_ack.call_args[1]
        assert ack_kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# _handle_po_cancel
# ---------------------------------------------------------------------------

class TestHandlePOCancel:
    def test_cancels_draft(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="draft")
        db.get.return_value = po

        update = _make_update("po_can", _po_hex())

        cancelled_po = _make_po(status="cancelled")

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
            patch("app.telegram.handlers.buttons.edit_message_text"),
        ):
            MockPOSvc.return_value.cancel.return_value = cancelled_po
            handle(update, user, db, ctx_svc, _make_settings())

        db.commit.assert_called()
        mock_ack.assert_called()
        ack_text = mock_ack.call_args[1]["text"]
        assert "cancel" in ack_text.lower()

    def test_received_po_cannot_be_cancelled(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="received")
        db.get.return_value = po

        update = _make_update("po_can", _po_hex())

        from app.services.purchase_order_service import POStatusError

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
        ):
            MockPOSvc.return_value.cancel.side_effect = POStatusError("Cannot cancel a received PO")
            handle(update, user, db, ctx_svc, _make_settings())

        ack_kwargs = mock_ack.call_args[1]
        assert ack_kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# _handle_po_add_item
# ---------------------------------------------------------------------------

class TestHandlePOAddItem:
    def test_sets_po_input_id_context(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="draft")
        db.get.return_value = po

        update = _make_update("po_add", _po_hex())

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.telegram.handlers.buttons.answer_callback_query"),
            patch("app.telegram.handlers.buttons.send_message") as mock_send,
        ):
            handle(update, user, db, ctx_svc, _make_settings())

        ctx_svc.set_fields.assert_called()
        set_fields_kwargs = ctx_svc.set_fields.call_args[1]
        assert "po_input_id" in set_fields_kwargs
        db.commit.assert_called()
        # Should send a prompt
        mock_send.assert_called()

    def test_non_draft_rejects_add_item(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="sent")
        db.get.return_value = po

        update = _make_update("po_add", _po_hex())

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
        ):
            handle(update, user, db, ctx_svc, _make_settings())

        ack_kwargs = mock_ack.call_args[1]
        assert ack_kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# _handle_po_receive
# ---------------------------------------------------------------------------

class TestHandlePOReceive:
    def test_marks_received_and_auto_stages(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        po = _make_po(status="sent")
        db.get.return_value = po

        update = _make_update("po_rcv", _po_hex())

        received_po = _make_po(status="received")
        received_po.received_at = MagicMock()
        received_po.received_at.strftime.return_value = "1 Feb 2026"

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
            patch("app.telegram.handlers.buttons.edit_message_text"),
            patch("app.telegram.handlers.buttons.send_message_with_keyboard") as mock_send_kb,
            patch("app.workers.ocr_tasks._build_review_text", return_value="Review text"),
            patch("app.workers.ocr_tasks._build_review_keyboard", return_value={"inline_keyboard": []}),
        ):
            MockPOSvc.return_value.mark_received.return_value = received_po
            db.scalars.return_value.all.return_value = []
            handle(update, user, db, ctx_svc, _make_settings())

        # Auto-staging: db.add should be called (staging record created)
        db.add.assert_called()
        db.commit.assert_called()
        mock_ack.assert_called()
        # Review keyboard sent
        mock_send_kb.assert_called()


# ---------------------------------------------------------------------------
# _handle_reorder_to_po
# ---------------------------------------------------------------------------

class TestHandleReorderToPO:
    def test_creates_draft_po_from_below_par_items(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()

        update = _make_update("reorder_po", _sup_hex())

        supplier = MagicMock()
        supplier.id = SUPPLIER_ID
        supplier.name = "Cheong Hing"

        # Make db.get return supplier
        db.get.return_value = supplier

        # Build a mock BelowParItem
        bi = MagicMock()
        bi.best_supplier_id = SUPPLIER_ID
        bi.item.id = uuid.uuid4()
        bi.item.name = "Chicken Breast"
        bi.gap = Decimal("3.0")
        bi.unit = "kg"
        bi.best_price_minor = 1250
        bi.best_price_exp = 2
        bi.best_price_currency = "SGD"

        po = _make_po(status="draft")

        with (
            patch("app.telegram.handlers.buttons._membership_flags", return_value=(True, True)),
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_ack,
            patch("app.telegram.handlers.buttons.send_message_with_keyboard") as mock_send_kb,
        ):
            MockParSvc.return_value.get_below_par_items.return_value = [bi]
            MockPOSvc.return_value.create_draft.return_value = po
            db.scalars.return_value.all.return_value = []
            handle(update, user, db, ctx_svc, _make_settings())

        db.commit.assert_called()
        mock_ack.assert_called()
        ack_text = mock_ack.call_args[1]["text"]
        assert "Draft PO created" in ack_text
        # PO displayed with keyboard
        mock_send_kb.assert_called()
