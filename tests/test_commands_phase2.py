"""Tests for Phase 2 commands: /par, /par set, /reorder, /order, /orders, /spend."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.telegram.handlers.commands import _parse_command, handle

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_commands.py)
# ---------------------------------------------------------------------------

RESTAURANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
SUPPLIER_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()


def _make_user(active_restaurant_id: uuid.UUID | None = RESTAURANT_ID) -> MagicMock:
    user = MagicMock()
    user.id = USER_ID
    user.chat_id = 100
    user.full_name = "John Smith"
    user.context = (
        {"active_restaurant_id": str(active_restaurant_id)} if active_restaurant_id else {}
    )
    return user


def _make_ctx_svc(user: MagicMock) -> MagicMock:
    ctx_svc = MagicMock()

    def _get_active(u):
        raw = (u.context or {}).get("active_restaurant_id")
        if raw is None:
            return None
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None

    def _set_fields(u, **kwargs):
        ctx = dict(u.context or {})
        for k, v in kwargs.items():
            if v is None:
                ctx.pop(k, None)
            else:
                ctx[k] = v
        u.context = ctx

    def _set_list_state(u, list_type, offset):
        _set_fields(u, last_list_type=list_type, last_list_offset=offset)

    ctx_svc.get_active_restaurant_id.side_effect = _get_active
    ctx_svc.set_fields.side_effect = _set_fields
    ctx_svc.set_list_state.side_effect = _set_list_state
    ctx_svc.get.return_value = {}
    return ctx_svc


def _make_db() -> MagicMock:
    db = MagicMock()
    db.commit = MagicMock()
    return db


def _make_settings() -> MagicMock:
    return MagicMock()


def _make_update(text: str) -> dict:
    return {"message": {"text": text}}


def _mock_restaurant_service(db=None) -> MagicMock:
    svc = MagicMock()
    svc.user_membership_exists.return_value = True
    return svc


def _make_par_item(
    name: str = "Chicken Breast",
    balance: float = 2.0,
    par_qty: float = 5.0,
    gap: float = 3.0,
    unit: str = "kg",
    supplier_name: str | None = "Cheong Hing",
    supplier_id: uuid.UUID | None = None,
) -> MagicMock:
    bi = MagicMock()
    item = MagicMock()
    item.name = name
    item.name_lower = name.lower()
    bi.item = item
    bi.balance = Decimal(str(balance))
    bi.par_qty = Decimal(str(par_qty))
    bi.gap = Decimal(str(gap))
    bi.unit = unit
    bi.best_supplier_name = supplier_name
    bi.best_supplier_id = supplier_id or (SUPPLIER_ID if supplier_name else None)
    bi.best_price_minor = 1250 if supplier_name else None
    bi.best_price_exp = 2 if supplier_name else None
    bi.best_price_currency = "SGD" if supplier_name else None
    return bi


# ---------------------------------------------------------------------------
# _parse_command — new patterns
# ---------------------------------------------------------------------------


class TestParseCommandPhase2:
    def test_par_set_matches_before_par(self):
        cmd, args = _parse_command("/par set chicken 5 kg")
        assert cmd == "par set"
        assert args == "chicken 5 kg"

    def test_par_no_args(self):
        cmd, args = _parse_command("/par")
        assert cmd == "par"
        assert args == ""

    def test_reorder(self):
        cmd, args = _parse_command("/reorder")
        assert cmd == "reorder"
        assert args == ""

    def test_orders_matches_before_order(self):
        cmd, args = _parse_command("/orders")
        assert cmd == "orders"
        assert args == ""

    def test_order_with_supplier(self):
        cmd, args = _parse_command("/order Cheong Hing")
        assert cmd == "order"
        assert args == "Cheong Hing"

    def test_spend_no_args(self):
        cmd, args = _parse_command("/spend")
        assert cmd == "spend"
        assert args == ""

    def test_spend_with_supplier(self):
        cmd, args = _parse_command("/spend Metro Fresh")
        assert cmd == "spend"
        assert args == "Metro Fresh"


# ---------------------------------------------------------------------------
# /par — view par levels
# ---------------------------------------------------------------------------


class TestParCommand:
    def test_sends_message_when_no_par_levels(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockParSvc.return_value.count_par_levels.return_value = 0
            handle(_make_update("/par"), user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        assert "No par levels" in mock_send.call_args[1]["text"]

    def test_sends_par_list_when_levels_exist(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        par = MagicMock()
        par.par_qty = Decimal("5.0")
        par.unit = "kg"
        item = MagicMock()
        item.name = "Chicken Breast"
        balance = MagicMock()
        balance.balance = Decimal("2.0")

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
            patch("app.telegram.handlers.commands.send_message_with_keyboard"),
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockParSvc.return_value.count_par_levels.return_value = 1
            MockParSvc.return_value.list_par_levels.return_value = [(par, item, balance)]
            handle(_make_update("/par"), user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        text = mock_send.call_args[1]["text"]
        assert "Par Levels" in text
        assert "Chicken Breast" in text


# ---------------------------------------------------------------------------
# /par set
# ---------------------------------------------------------------------------


class TestParSetCommand:
    def test_missing_args_shows_usage(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            handle(_make_update("/par set"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "Usage" in text

    def test_invalid_quantity_shows_error(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            handle(_make_update("/par set chicken abc kg"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "Invalid quantity" in text

    def test_no_item_match_shows_error(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.InventoryService") as MockInvSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockInvSvc.return_value.fuzzy_match_item.return_value = []
            handle(_make_update("/par set chicken 5 kg"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "No inventory item" in text

    def test_sets_par_level_and_confirms(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        item = MagicMock()
        item.id = ITEM_ID
        item.name = "Chicken Breast"
        par_mock = MagicMock()

        db.scalar.return_value = Decimal("2.0")  # current balance

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.InventoryService") as MockInvSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockInvSvc.return_value.fuzzy_match_item.return_value = [(item, 0.9)]
            MockParSvc.return_value.set_par.return_value = (par_mock, True)
            handle(_make_update("/par set chicken 5 kg"), user, db, ctx_svc, settings)

        db.commit.assert_called()
        text = mock_send.call_args[1]["text"]
        assert "Par level set" in text
        assert "Chicken Breast" in text


# ---------------------------------------------------------------------------
# /reorder
# ---------------------------------------------------------------------------


class TestReorderCommand:
    def test_all_at_par_shows_ok_message(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockParSvc.return_value.get_below_par_items.return_value = []
            handle(_make_update("/reorder"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "at or above par" in text

    def test_below_par_items_shown_with_supplier_button(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        bi = _make_par_item()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.services.money.to_display") as mock_to_display,
            patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send_kb,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockParSvc.return_value.get_below_par_items.return_value = [bi]
            mock_to_display.return_value = Decimal("12.50")
            handle(_make_update("/reorder"), user, db, ctx_svc, settings)

        # Should send with keyboard (supplier button)
        mock_send_kb.assert_called_once()
        text = mock_send_kb.call_args[1]["text"]
        assert "below par" in text
        assert "Chicken Breast" in text

    def test_below_par_no_price_data_still_shows(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        bi = _make_par_item(supplier_name=None)

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.par_service.ParService") as MockParSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockParSvc.return_value.get_below_par_items.return_value = [bi]
            handle(_make_update("/reorder"), user, db, ctx_svc, settings)

        # Without supplier buttons, should just send a plain message
        text = mock_send.call_args[1]["text"]
        assert "no price data" in text


# ---------------------------------------------------------------------------
# /orders
# ---------------------------------------------------------------------------


class TestOrdersCommand:
    def test_shows_empty_state(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockPOSvc.return_value.count_orders.return_value = 0
            handle(_make_update("/orders"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "No purchase orders" in text

    def test_shows_orders_list(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        po = MagicMock()
        po.status = "draft"
        po.supplier_name = "Cheong Hing"
        po.created_at = MagicMock()
        po.created_at.strftime.return_value = "1 Feb"

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
            patch("app.telegram.handlers.commands.send_message_with_keyboard"),
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockPOSvc.return_value.count_orders.return_value = 1
            MockPOSvc.return_value.list_orders.return_value = [po]
            handle(_make_update("/orders"), user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        text = mock_send.call_args[1]["text"]
        assert "Purchase Orders" in text
        assert "Cheong Hing" in text


# ---------------------------------------------------------------------------
# /order <supplier>
# ---------------------------------------------------------------------------


class TestOrderCommand:
    def test_missing_args_shows_usage(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            handle(_make_update("/order"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "Usage" in text

    def test_unknown_supplier_shows_error(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.SupplierService") as MockSupSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockSupSvc.return_value.fuzzy_search_for_restaurant.return_value = []
            handle(_make_update("/order Unknown Supplier"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "No supplier found" in text

    def test_creates_draft_po_and_shows_keyboard(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        supplier = MagicMock()
        supplier.id = SUPPLIER_ID
        supplier.name = "Cheong Hing"

        po = MagicMock()
        po.id = PO_ID

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.SupplierService") as MockSupSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send_kb,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockSupSvc.return_value.fuzzy_search_for_restaurant.return_value = [
                (supplier, 0.9)
            ]
            MockPOSvc.return_value.create_draft.return_value = po
            handle(_make_update("/order Cheong Hing"), user, db, ctx_svc, settings)

        db.commit.assert_called()
        mock_send_kb.assert_called_once()
        text = mock_send_kb.call_args[1]["text"]
        assert "Cheong Hing" in text
        keyboard = mock_send_kb.call_args[1]["reply_markup"]
        assert "inline_keyboard" in keyboard


# ---------------------------------------------------------------------------
# /spend
# ---------------------------------------------------------------------------


class TestSpendCommand:
    def test_no_received_orders_shows_empty(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        from app.services.purchase_order_service import SpendSummary

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockPOSvc.return_value.get_spend_summary.return_value = SpendSummary(
                period_label="Feb 2026", rows=[], grand_total=Decimal("0")
            )
            handle(_make_update("/spend"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "No received orders" in text

    def test_shows_spend_summary(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        from app.services.purchase_order_service import SpendRow, SpendSummary

        row = SpendRow(
            supplier_name="Cheong Hing",
            supplier_id=SUPPLIER_ID,
            total_display=Decimal("183.40"),
            currency="SGD",
            order_count=3,
        )
        summary = SpendSummary(
            period_label="Feb 2026", rows=[row], grand_total=Decimal("183.40")
        )

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockPOSvc.return_value.get_spend_summary.return_value = summary
            handle(_make_update("/spend"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "Spend" in text
        assert "Cheong Hing" in text
        assert "183" in text

    def test_supplier_filter_applied(self):
        user = _make_user()
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()
        settings = _make_settings()

        from app.services.purchase_order_service import SpendRow, SpendSummary

        row1 = SpendRow("Cheong Hing", SUPPLIER_ID, Decimal("100"), "SGD", 2)
        row2 = SpendRow("Metro Fresh", uuid.uuid4(), Decimal("50"), "SGD", 1)
        summary = SpendSummary("Last 3 months", [row1, row2], Decimal("150"))

        supplier = MagicMock()
        supplier.id = SUPPLIER_ID
        supplier.name = "Cheong Hing"

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockRSvc,
            patch("app.telegram.handlers.commands.SupplierService") as MockSupSvc,
            patch("app.services.purchase_order_service.PurchaseOrderService") as MockPOSvc,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockRSvc.return_value.user_membership_exists.return_value = True
            MockSupSvc.return_value.fuzzy_search_for_restaurant.return_value = [
                (supplier, 0.9)
            ]
            MockPOSvc.return_value.get_spend_summary.return_value = summary
            handle(_make_update("/spend Cheong Hing"), user, db, ctx_svc, settings)

        text = mock_send.call_args[1]["text"]
        assert "Cheong Hing" in text
        assert "Metro Fresh" not in text
