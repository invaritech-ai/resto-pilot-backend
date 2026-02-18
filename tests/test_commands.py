"""Tests for app/telegram/handlers/commands.py."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.telegram.handlers.commands import _require_active_restaurant, handle

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

RESTAURANT_ID = uuid.uuid4()
RESTAURANT_ID_2 = uuid.uuid4()
OTHER_RESTAURANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _make_user(active_restaurant_id: uuid.UUID | None = RESTAURANT_ID) -> MagicMock:
    user = MagicMock()
    user.id = USER_ID
    user.chat_id = 100
    user.full_name = "John Smith"
    user.username = "johnsmith"
    user.context = (
        {"active_restaurant_id": str(active_restaurant_id)}
        if active_restaurant_id
        else {}
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

    def _set_active(u, rid):
        _set_fields(u, active_restaurant_id=str(rid))

    def _set_numbered(u, items):
        _set_fields(u, numbered_items=[str(i) for i in items])

    def _set_list_state(u, list_type, offset):
        _set_fields(u, last_list_type=list_type, last_list_offset=offset)

    ctx_svc.get_active_restaurant_id.side_effect = _get_active
    ctx_svc.set_fields.side_effect = _set_fields
    ctx_svc.set_active_restaurant.side_effect = _set_active
    ctx_svc.set_numbered_items.side_effect = _set_numbered
    ctx_svc.set_list_state.side_effect = _set_list_state
    return ctx_svc


def _make_db() -> MagicMock:
    db = MagicMock()
    db.commit = MagicMock()
    return db


def _make_settings() -> MagicMock:
    return MagicMock()


def _make_update(text: str) -> dict:
    return {"message": {"text": text}}


def _mock_restaurant(name: str = "The Blue Bistro", rid: uuid.UUID = RESTAURANT_ID):
    r = MagicMock()
    r.id = rid
    r.name = name
    return r


def _mock_membership(is_owner: bool = True, joined: str = "Feb 2026"):
    m = MagicMock()
    m.is_owner = is_owner
    m.joined_at = MagicMock()
    m.joined_at.strftime.return_value = joined
    return m


# ---------------------------------------------------------------------------
# _require_active_restaurant — membership valid
# ---------------------------------------------------------------------------


class TestRequireActiveRestaurantValidMembership:
    def test_returns_active_restaurant_id_when_member(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.user_membership_exists.return_value = True
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result == RESTAURANT_ID
        MockSvc.return_value.user_membership_exists.assert_called_once_with(
            restaurant_id=RESTAURANT_ID, user_id=USER_ID
        )
        assert user.context.get("active_restaurant_id") == str(RESTAURANT_ID)
        db.commit.assert_not_called()


class TestRequireActiveRestaurantStaleMembership:
    def test_clears_stale_context_and_falls_through_when_not_member(self):
        user = _make_user(OTHER_RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.user_membership_exists.return_value = False
            MockSvc.return_value.list_for_user.return_value = []
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result is None
        assert "active_restaurant_id" not in user.context
        db.commit.assert_called_once()

    def test_stale_context_clears_then_auto_sets_if_one_restaurant_exists(self):
        user = _make_user(OTHER_RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        mock_restaurant = MagicMock()
        mock_restaurant.id = RESTAURANT_ID

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.user_membership_exists.return_value = False
            MockSvc.return_value.list_for_user.return_value = [
                (mock_restaurant, MagicMock())
            ]
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result == RESTAURANT_ID
        assert user.context.get("active_restaurant_id") == str(RESTAURANT_ID)


class TestRequireActiveRestaurantNotSet:
    def test_returns_none_when_no_context_and_no_restaurants(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.list_for_user.return_value = []
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result is None

    def test_auto_sets_and_returns_when_exactly_one_restaurant(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        mock_restaurant = MagicMock()
        mock_restaurant.id = RESTAURANT_ID

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.list_for_user.return_value = [
                (mock_restaurant, MagicMock())
            ]
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result == RESTAURANT_ID
        assert user.context.get("active_restaurant_id") == str(RESTAURANT_ID)

    def test_returns_none_when_multiple_restaurants_and_no_context(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        r1, r2 = MagicMock(), MagicMock()
        r1.id, r2.id = uuid.uuid4(), uuid.uuid4()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc:
            MockSvc.return_value.list_for_user.return_value = [
                (r1, MagicMock()),
                (r2, MagicMock()),
            ]
            result = _require_active_restaurant(user, ctx_svc, db, _make_settings())

        assert result is None


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------


class TestProfileCommand:
    def test_profile_no_restaurants_redirects_to_onboarding(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/profile"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "set up" in text.lower() or "start" in text.lower()

    def test_profile_shows_owner_details(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant("The Blue Bistro", RESTAURANT_ID)
        membership = _mock_membership(is_owner=True, joined="Feb 2026")

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, membership)]
            handle(_make_update("/profile"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "John Smith" in text
        assert "The Blue Bistro" in text
        assert "Owner" in text
        assert "Feb 2026" in text

    def test_profile_shows_member_role(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        membership = _mock_membership(is_owner=False)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, membership)]
            handle(_make_update("/profile"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Member" in text
        assert "Owner" not in text

    def test_profile_no_username_omits_username_line(self):
        user = _make_user(RESTAURANT_ID)
        user.username = None
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        membership = _mock_membership()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, membership)]
            handle(_make_update("/profile"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Username" not in text

    def test_profile_shows_username_when_set(self):
        user = _make_user(RESTAURANT_ID)
        user.username = "johnsmith"
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        membership = _mock_membership()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, membership)]
            handle(_make_update("/profile"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "@johnsmith" in text


# ---------------------------------------------------------------------------
# /team
# ---------------------------------------------------------------------------


class TestTeamCommand:
    def test_team_no_active_restaurant_prompts_switch(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = False
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/team"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "switch" in text.lower() or "no active" in text.lower()

    def test_team_shows_members_with_roles(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        owner_user = MagicMock()
        owner_user.full_name = "Alice Owner"
        owner_mem = _mock_membership(is_owner=True, joined="Jan 2026")
        staff_user = MagicMock()
        staff_user.full_name = "Bob Staff"
        staff_mem = _mock_membership(is_owner=False, joined="Feb 2026")

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, owner_mem)]
            MockSvc.return_value.list_members.return_value = [
                (owner_user, owner_mem),
                (staff_user, staff_mem),
            ]
            handle(_make_update("/team"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Alice Owner" in text
        assert "Owner" in text
        assert "Bob Staff" in text
        assert "Member" in text

    def test_team_single_member_count(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        owner_user = MagicMock()
        owner_user.full_name = "Alice Owner"
        owner_mem = _mock_membership(is_owner=True)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, owner_mem)]
            MockSvc.return_value.list_members.return_value = [(owner_user, owner_mem)]
            handle(_make_update("/team"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "1 member total" in text

    def test_team_eleven_members_shows_pagination(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        owner_mem = _mock_membership(is_owner=True)
        members = []
        for i in range(11):
            u = MagicMock()
            u.full_name = f"Member {i}"
            u.id = uuid.uuid4()
            m = _mock_membership(is_owner=(i == 0))
            members.append((u, m))

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send, \
             patch("app.telegram.handlers.commands.send_message"):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, owner_mem)]
            MockSvc.return_value.list_members.return_value = members
            handle(_make_update("/team"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Page 1/2" in text
        assert "11 members total" in text

    def test_team_stores_member_ids_in_context(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant()
        member_ids = [uuid.uuid4() for _ in range(3)]
        members = []
        for mid in member_ids:
            u = MagicMock()
            u.id = mid
            u.full_name = f"User {mid}"
            members.append((u, _mock_membership()))

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message"):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, _mock_membership())]
            MockSvc.return_value.list_members.return_value = members
            handle(_make_update("/team"), user, db, ctx_svc, _make_settings())

        stored = user.context.get("numbered_items", [])
        assert [uuid.UUID(s) for s in stored] == member_ids


class TestInventoryCommand:
    def test_no_active_restaurant_prompts_setup(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/inventory"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "no active" in text.lower() or "/start" in text.lower()

    def test_empty_inventory_shows_onboarding_hint(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.count_items.return_value = 0
            handle(_make_update("/inventory"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No inventory data yet" in text

    def test_lists_items_with_balances(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        item1 = MagicMock()
        item1.id = uuid.uuid4()
        item1.name = "Chicken Breast"
        item1.unit = "kg"

        balance1 = MagicMock()
        balance1.balance = 10.5

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.count_items.return_value = 1
            MockInv.return_value.list_items.return_value = [(item1, balance1)]
            handle(_make_update("/inventory"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Chicken Breast" in text
        assert "10.5" in text
        assert "kg" in text

    def test_negative_balance_shows_warning(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        item1 = MagicMock()
        item1.id = uuid.uuid4()
        item1.name = "Olive Oil"
        item1.unit = "L"

        balance1 = MagicMock()
        balance1.balance = -2.5

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.count_items.return_value = 1
            MockInv.return_value.list_items.return_value = [(item1, balance1)]
            handle(_make_update("/inventory"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "⚠️" in text
        assert "Olive Oil" in text


class TestBalanceCommand:
    def test_no_active_restaurant_prompts_setup(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "no active" in text.lower() or "/start" in text.lower()

    def test_empty_inventory_shows_onboarding_hint(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        from app.services.inventory_service import BalanceSummary
        summary = BalanceSummary(total_items=0, zero_stock_count=0, negative_count=0)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.get_balance_summary.return_value = summary
            MockInv.return_value.list_negative_items.return_value = []
            MockInv.return_value.list_zero_stock_items.return_value = []
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No inventory data yet" in text

    def test_shows_summary_counts(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        from app.services.inventory_service import BalanceSummary
        summary = BalanceSummary(total_items=15, zero_stock_count=2, negative_count=1)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.get_balance_summary.return_value = summary
            MockInv.return_value.list_negative_items.return_value = []
            MockInv.return_value.list_zero_stock_items.return_value = []
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "15" in text
        assert "2" in text
        assert "1" in text

    def test_includes_item_breakdown_sections(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        from app.services.inventory_service import BalanceSummary
        summary = BalanceSummary(total_items=20, zero_stock_count=2, negative_count=3)
        neg_item = MagicMock()
        neg_item.name = "Olive Oil"
        neg_item.unit = "L"
        neg_balance = MagicMock()
        neg_balance.balance = -4
        zero_item = MagicMock()
        zero_item.name = "Chicken Breast"
        zero_item.unit = "kg"
        zero_balance = MagicMock()
        zero_balance.balance = 0

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.get_balance_summary.return_value = summary
            MockInv.return_value.list_negative_items.return_value = [(neg_item, neg_balance)]
            MockInv.return_value.list_zero_stock_items.return_value = [(zero_item, zero_balance)]
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Most negative items" in text
        assert "Olive Oil" in text
        assert "Zero-stock items" in text
        assert "Chicken Breast" in text

    def test_zero_stock_shows_warning_flag(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        from app.services.inventory_service import BalanceSummary
        summary = BalanceSummary(total_items=5, zero_stock_count=3, negative_count=0)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.get_balance_summary.return_value = summary
            MockInv.return_value.list_negative_items.return_value = []
            MockInv.return_value.list_zero_stock_items.return_value = []
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "⚠️" in text

    def test_no_warning_when_all_healthy(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        from app.services.inventory_service import BalanceSummary
        summary = BalanceSummary(total_items=10, zero_stock_count=0, negative_count=0)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.commands.InventoryService") as MockInv, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockRest.return_value.user_membership_exists.return_value = True
            MockInv.return_value.get_balance_summary.return_value = summary
            MockInv.return_value.list_negative_items.return_value = []
            MockInv.return_value.list_zero_stock_items.return_value = []
            handle(_make_update("/balance"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "⚠️" not in text


# ---------------------------------------------------------------------------
# /outlets
# ---------------------------------------------------------------------------


class TestOutletsCommand:
    def test_outlets_no_restaurants(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/outlets"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "start" in text.lower() or "not part of" in text.lower()

    def test_outlets_single_restaurant_marked_active(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        restaurant = _mock_restaurant("The Blue Bistro", RESTAURANT_ID)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [(restaurant, _mock_membership())]
            handle(_make_update("/outlets"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "✅" in text
        assert "The Blue Bistro" in text

    def test_outlets_multiple_restaurants_active_marked(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        r1 = _mock_restaurant("The Blue Bistro", RESTAURANT_ID)
        r2 = _mock_restaurant("Corner Cafe", RESTAURANT_ID_2)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc.return_value.list_for_user.return_value = [
                (r1, _mock_membership()),
                (r2, _mock_membership()),
            ]
            handle(_make_update("/outlets"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "✅" in text
        assert "The Blue Bistro" in text
        assert "Corner Cafe" in text
        # Only one ✅
        assert text.count("✅") == 1



# ---------------------------------------------------------------------------
# /products
# ---------------------------------------------------------------------------


def _mock_price(item_name="Chicken Breast", price_minor=850, price_exp=2, currency="USD", unit="kg"):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.item_name = item_name
    p.price_minor = price_minor
    p.price_exp = price_exp
    p.currency = currency
    p.unit = unit
    return p


class TestProductsCommand:
    def test_products_no_active_restaurant(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = False
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "switch" in text.lower() or "no active" in text.lower()

    def test_products_no_data(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = []
            MockSvc2.return_value.count_products_for_restaurant.return_value = 0
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No products" in text

    def test_products_single_supplier_listed(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.name = "ABC Wholesalers"
        price = _mock_price("Chicken Breast", 850, 2, "USD", "kg")

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = [(supplier, price)]
            MockSvc2.return_value.count_products_for_restaurant.return_value = 1
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "ABC Wholesalers" in text
        assert "Chicken Breast" in text
        assert "8.50 USD" in text
        assert "/kg" in text

    def test_products_groups_by_supplier(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        s1 = MagicMock()
        s1.name = "ABC Wholesalers"
        s2 = MagicMock()
        s2.name = "Fresh Co"
        p1 = _mock_price("Chicken Breast")
        p2 = _mock_price("Tomatoes", 210, 2, "USD", "kg")

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = [
                (s1, p1), (s2, p2)
            ]
            MockSvc2.return_value.count_products_for_restaurant.return_value = 2
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "ABC Wholesalers" in text
        assert "Fresh Co" in text
        assert "Chicken Breast" in text
        assert "Tomatoes" in text

    def test_products_pagination_shown_when_more_than_10(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.name = "Big Supplier"
        page = [(supplier, _mock_price(f"Item {i}")) for i in range(10)]

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send, \
             patch("app.telegram.handlers.commands.send_message"):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = page
            MockSvc2.return_value.count_products_for_restaurant.return_value = 11
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Page 1/2" in text

    def test_products_new_list_disables_old_list_keyboard(self):
        user = _make_user(RESTAURANT_ID)
        user.context["active_list_message_id"] = 111
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.name = "Big Supplier"
        page = [(supplier, _mock_price(f"Item {i}")) for i in range(10)]

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockSvc,
            patch("app.telegram.handlers.commands.SupplierService") as MockSvc2,
            patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send_kb,
            patch("app.telegram.handlers.commands.edit_message_reply_markup") as mock_edit_markup,
            patch("app.telegram.handlers.commands.send_message"),
        ):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = page
            MockSvc2.return_value.count_products_for_restaurant.return_value = 11
            mock_send_kb.return_value = 222
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        mock_edit_markup.assert_called_once_with(
            chat_id=user.chat_id,
            message_id=111,
            reply_markup={"inline_keyboard": []},
            settings=ANY,
        )
        assert user.context.get("active_list_message_id") == 222

    def test_products_stores_price_ids_in_context(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.name = "Supplier"
        price_ids = [uuid.uuid4() for _ in range(3)]
        products = []
        for pid in price_ids:
            p = _mock_price()
            p.id = pid
            products.append((supplier, p))

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message"):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.list_products_for_restaurant.return_value = products
            MockSvc2.return_value.count_products_for_restaurant.return_value = 3
            handle(_make_update("/products"), user, db, ctx_svc, _make_settings())

        stored = user.context.get("numbered_items", [])
        assert [uuid.UUID(s) for s in stored] == price_ids


# ---------------------------------------------------------------------------
# /prices
# ---------------------------------------------------------------------------


class TestPricesCommand:
    def test_prices_no_active_restaurant(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = False
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/prices ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "switch" in text.lower() or "no active" in text.lower()

    def test_prices_no_args_shows_usage(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            handle(_make_update("/prices"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Usage" in text or "usage" in text

    def test_prices_no_supplier_match(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = []
            handle(_make_update("/prices UnknownCo"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No supplier found" in text

    def test_prices_no_match_shows_did_you_mean_suggestions(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        suggestion_1 = MagicMock()
        suggestion_1.name = "SoHaVegetables"
        suggestion_2 = MagicMock()
        suggestion_2.name = "Soho Greens"

        with (
            patch("app.telegram.handlers.commands.RestaurantService") as MockSvc,
            patch("app.telegram.handlers.commands.SupplierService") as MockSvc2,
            patch("app.telegram.handlers.commands.send_message") as mock_send,
        ):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.side_effect = [
                [],
                [(suggestion_1, 0.33), (suggestion_2, 0.27)],
            ]
            handle(_make_update("/prices Soha"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Did you mean" in text
        assert "SoHaVegetables" in text
        assert "Soho Greens" in text

    def test_prices_supplier_found_no_prices(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.name = "ABC Wholesalers"

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = [(supplier, 0.9)]
            MockSvc2.return_value.list_prices_for_supplier.return_value = []
            MockSvc2.return_value.count_prices_for_supplier.return_value = 0
            handle(_make_update("/prices ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No prices found" in text

    def test_prices_shows_items_with_dates(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.name = "ABC Wholesalers"
        price = _mock_price("Chicken Breast", 850, 2, "USD", "kg")
        eff_date = datetime.date(2026, 2, 15)

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = [(supplier, 0.9)]
            MockSvc2.return_value.list_prices_for_supplier.return_value = [(price, eff_date)]
            MockSvc2.return_value.count_prices_for_supplier.return_value = 1
            MockSvc2.return_value.get_price_list_meta.return_value = (None, None)
            handle(_make_update("/prices ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "ABC Wholesalers" in text
        assert "Chicken Breast" in text
        assert "8.50 USD" in text
        assert "updated" in text
        assert "Feb 15" in text

    def test_prices_no_date_omits_date(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.name = "ABC Wholesalers"
        price = _mock_price("Olive Oil", 320, 2, "USD", "L")

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = [(supplier, 0.9)]
            MockSvc2.return_value.list_prices_for_supplier.return_value = [(price, None)]
            MockSvc2.return_value.count_prices_for_supplier.return_value = 1
            MockSvc2.return_value.get_price_list_meta.return_value = (None, None)
            handle(_make_update("/prices ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "updated" not in text
        assert "Olive Oil" in text

    def test_prices_pagination_shown_when_more_than_10(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.name = "Big Supplier"
        page = [(_mock_price(f"Item {i}"), None) for i in range(10)]

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message_with_keyboard") as mock_send, \
             patch("app.telegram.handlers.commands.send_message"):
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = [(supplier, 0.9)]
            MockSvc2.return_value.list_prices_for_supplier.return_value = page
            MockSvc2.return_value.count_prices_for_supplier.return_value = 11
            MockSvc2.return_value.get_price_list_meta.return_value = (None, None)
            handle(_make_update("/prices Big"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "Page 1/2" in text

    def test_prices_uses_restaurant_scoped_search(self):
        """Regression: /prices must use fuzzy_search_for_restaurant, not fuzzy_search.

        A supplier that exists globally but is not linked to the active restaurant
        must return "No supplier found", not leak prices from another restaurant.
        """
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            # Restaurant-scoped search finds nothing (supplier exists globally but not linked)
            MockSvc2.return_value.fuzzy_search_for_restaurant.return_value = []
            handle(_make_update("/prices GlobalOnlyCo"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "No supplier found" in text
        # Global fuzzy_search must NOT have been called for /prices
        MockSvc2.return_value.fuzzy_search.assert_not_called()


# ---------------------------------------------------------------------------
# /add supplier
# ---------------------------------------------------------------------------


class TestAddSupplierCommand:
    def test_add_supplier_no_active_restaurant(self):
        user = _make_user(None)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.list_for_user.return_value = []
            handle(_make_update("/add supplier ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "switch" in text.lower() or "no active" in text.lower()

    def test_add_supplier_no_args_prompts_name(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            handle(_make_update("/add supplier"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "supplier name" in text.lower() or "example" in text.lower()

    def test_add_supplier_no_match_creates_new(self):
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        new_supplier = MagicMock()
        new_supplier.name = "Brand New Co"

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search.return_value = []
            MockSvc2.return_value.create.return_value = new_supplier
            handle(_make_update("/add supplier Brand New Co"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "✅" in text
        assert "Brand New Co" in text
        db.commit.assert_called()

    def test_add_supplier_fuzzy_match_links_existing(self):
        """Regression P0: fuzzy match → link existing supplier, no yes/no prompt."""
        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        existing = MagicMock()
        existing.id = uuid.uuid4()
        existing.name = "ABC Wholesalers"

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search.return_value = [(existing, 0.88)]
            handle(_make_update("/add supplier ABC Whol"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "✅" in text
        assert "ABC Wholesalers" in text
        # Must call link(), not create()
        MockSvc2.return_value.link.assert_called_once_with(
            supplier_id=existing.id,
            restaurant_id=RESTAURANT_ID,
            user_id=USER_ID,
        )
        MockSvc2.return_value.create.assert_not_called()
        db.commit.assert_called()

    def test_add_supplier_already_linked_shows_info_message(self):
        """Regression P0: already-linked supplier → informative message, no error."""
        from app.services.supplier_service import AlreadyLinkedError

        user = _make_user(RESTAURANT_ID)
        ctx_svc = _make_ctx_svc(user)
        db = _make_db()

        existing = MagicMock()
        existing.id = uuid.uuid4()
        existing.name = "ABC Wholesalers"

        with patch("app.telegram.handlers.commands.RestaurantService") as MockSvc, \
             patch("app.telegram.handlers.commands.SupplierService") as MockSvc2, \
             patch("app.telegram.handlers.commands.send_message") as mock_send:
            MockSvc.return_value.user_membership_exists.return_value = True
            MockSvc2.return_value.fuzzy_search.return_value = [(existing, 0.88)]
            MockSvc2.return_value.link.side_effect = AlreadyLinkedError("already linked")
            handle(_make_update("/add supplier ABC"), user, db, ctx_svc, _make_settings())

        text = mock_send.call_args[1]["text"]
        assert "already linked" in text.lower() or "ℹ️" in text
        db.commit.assert_not_called()
