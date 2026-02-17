"""Tests for app/services/context_service.py.

Uses a lightweight stub instead of a real DB session — context_service only
calls session.add() and session.flush(), which we can mock trivially.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.context_service import ContextService, _NAV_FIELDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_user(context: dict | None = None):
    """Return a mock User with a mutable context dict."""
    user = MagicMock()
    user.context = dict(context) if context else None
    return user


def make_service():
    """Return a ContextService with a no-op session mock."""
    session = MagicMock()
    session.flush = MagicMock()
    session.add = MagicMock()
    return ContextService(session)


R_ID = uuid.uuid4()
S_ID = uuid.uuid4()
ITEM_A = uuid.uuid4()
ITEM_B = uuid.uuid4()


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_empty_dict_when_context_is_none(self):
        svc = make_service()
        user = make_user(None)
        assert svc.get(user) == {}

    def test_returns_context_dict(self):
        svc = make_service()
        user = make_user({"active_restaurant_id": str(R_ID)})
        assert svc.get(user) == {"active_restaurant_id": str(R_ID)}


# ---------------------------------------------------------------------------
# get_active_restaurant_id
# ---------------------------------------------------------------------------

class TestGetActiveRestaurantId:
    def test_returns_uuid_when_set(self):
        svc = make_service()
        user = make_user({"active_restaurant_id": str(R_ID)})
        assert svc.get_active_restaurant_id(user) == R_ID

    def test_returns_none_when_absent(self):
        svc = make_service()
        user = make_user({})
        assert svc.get_active_restaurant_id(user) is None

    def test_returns_none_for_invalid_uuid(self):
        svc = make_service()
        user = make_user({"active_restaurant_id": "not-a-uuid"})
        assert svc.get_active_restaurant_id(user) is None

    def test_returns_none_when_context_is_none(self):
        svc = make_service()
        user = make_user(None)
        assert svc.get_active_restaurant_id(user) is None


# ---------------------------------------------------------------------------
# get_active_staging_id
# ---------------------------------------------------------------------------

class TestGetActiveStagingId:
    def test_returns_uuid_when_set(self):
        svc = make_service()
        user = make_user({"active_staging_id": str(S_ID)})
        assert svc.get_active_staging_id(user) == S_ID

    def test_returns_none_when_absent(self):
        svc = make_service()
        user = make_user({})
        assert svc.get_active_staging_id(user) is None


# ---------------------------------------------------------------------------
# get_numbered_items
# ---------------------------------------------------------------------------

class TestGetNumberedItems:
    def test_returns_uuids(self):
        svc = make_service()
        user = make_user({"numbered_items": [str(ITEM_A), str(ITEM_B)]})
        assert svc.get_numbered_items(user) == [ITEM_A, ITEM_B]

    def test_returns_empty_list_when_absent(self):
        svc = make_service()
        user = make_user({})
        assert svc.get_numbered_items(user) == []

    def test_skips_invalid_uuids(self):
        svc = make_service()
        user = make_user({"numbered_items": [str(ITEM_A), "bad", str(ITEM_B)]})
        assert svc.get_numbered_items(user) == [ITEM_A, ITEM_B]


# ---------------------------------------------------------------------------
# get_last_list_type / get_last_list_offset
# ---------------------------------------------------------------------------

class TestGetListState:
    def test_last_list_type(self):
        svc = make_service()
        user = make_user({"last_list_type": "suppliers"})
        assert svc.get_last_list_type(user) == "suppliers"

    def test_last_list_type_absent(self):
        svc = make_service()
        user = make_user({})
        assert svc.get_last_list_type(user) is None

    def test_last_list_offset_default_is_zero(self):
        svc = make_service()
        user = make_user({})
        assert svc.get_last_list_offset(user) == 0

    def test_last_list_offset_value(self):
        svc = make_service()
        user = make_user({"last_list_offset": 20})
        assert svc.get_last_list_offset(user) == 20


# ---------------------------------------------------------------------------
# set_fields
# ---------------------------------------------------------------------------

class TestSetFields:
    def test_sets_new_field(self):
        svc = make_service()
        user = make_user(None)
        svc.set_fields(user, active_staging_id=str(S_ID))
        assert user.context["active_staging_id"] == str(S_ID)

    def test_merges_without_overwriting_other_fields(self):
        svc = make_service()
        user = make_user({"active_restaurant_id": str(R_ID)})
        svc.set_fields(user, active_staging_id=str(S_ID))
        assert user.context["active_restaurant_id"] == str(R_ID)
        assert user.context["active_staging_id"] == str(S_ID)

    def test_none_value_removes_key(self):
        svc = make_service()
        user = make_user({"active_staging_id": str(S_ID)})
        svc.set_fields(user, active_staging_id=None)
        assert "active_staging_id" not in user.context

    def test_none_value_for_absent_key_is_safe(self):
        svc = make_service()
        user = make_user({})
        svc.set_fields(user, active_staging_id=None)  # should not raise

    def test_flush_is_called(self):
        svc = make_service()
        user = make_user({})
        svc.set_fields(user, last_list_type="suppliers")
        svc.session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# set_active_restaurant
# ---------------------------------------------------------------------------

class TestSetActiveRestaurant:
    def test_stores_as_string(self):
        svc = make_service()
        user = make_user(None)
        svc.set_active_restaurant(user, R_ID)
        assert user.context["active_restaurant_id"] == str(R_ID)

    def test_accepts_string(self):
        svc = make_service()
        user = make_user(None)
        svc.set_active_restaurant(user, str(R_ID))
        assert user.context["active_restaurant_id"] == str(R_ID)


# ---------------------------------------------------------------------------
# set_active_staging / set_numbered_items / set_list_state
# ---------------------------------------------------------------------------

class TestSetHelpers:
    def test_set_active_staging(self):
        svc = make_service()
        user = make_user(None)
        svc.set_active_staging(user, S_ID)
        assert user.context["active_staging_id"] == str(S_ID)

    def test_set_numbered_items(self):
        svc = make_service()
        user = make_user(None)
        svc.set_numbered_items(user, [ITEM_A, ITEM_B])
        assert user.context["numbered_items"] == [str(ITEM_A), str(ITEM_B)]

    def test_set_list_state(self):
        svc = make_service()
        user = make_user(None)
        svc.set_list_state(user, "suppliers", 10)
        assert user.context["last_list_type"] == "suppliers"
        assert user.context["last_list_offset"] == 10


# ---------------------------------------------------------------------------
# clear_navigation
# ---------------------------------------------------------------------------

class TestClearNavigation:
    def test_clears_all_nav_fields(self):
        svc = make_service()
        user = make_user({
            "active_restaurant_id": str(R_ID),
            "last_list_type": "suppliers",
            "last_list_offset": 20,
            "numbered_items": [str(ITEM_A)],
            "active_staging_id": str(S_ID),
        })
        svc.clear_navigation(user)
        for field in _NAV_FIELDS:
            assert field not in user.context, f"{field} should have been cleared"

    def test_preserves_active_restaurant_id(self):
        svc = make_service()
        user = make_user({
            "active_restaurant_id": str(R_ID),
            "last_list_type": "suppliers",
        })
        svc.clear_navigation(user)
        assert user.context["active_restaurant_id"] == str(R_ID)

    def test_safe_on_empty_context(self):
        svc = make_service()
        user = make_user(None)
        svc.clear_navigation(user)  # should not raise
        assert user.context == {}

    def test_flush_is_called(self):
        svc = make_service()
        user = make_user({})
        svc.clear_navigation(user)
        svc.session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------

class TestClearAll:
    def test_wipes_everything(self):
        svc = make_service()
        user = make_user({
            "active_restaurant_id": str(R_ID),
            "last_list_type": "suppliers",
            "active_staging_id": str(S_ID),
        })
        svc.clear_all(user)
        assert user.context == {}

    def test_flush_is_called(self):
        svc = make_service()
        user = make_user({"active_restaurant_id": str(R_ID)})
        svc.clear_all(user)
        svc.session.flush.assert_called_once()
