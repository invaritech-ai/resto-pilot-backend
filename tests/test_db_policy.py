import pytest

from app.policies.db_policy import normalize_db_action, validate_db_action
from app.schemas.db_action import DBAction


def _action(**overrides) -> DBAction:
    base = {
        "action_id": "a1",
        "intent": "test",
        "crud": "read",
        "table": "users",
        "role": "staff",
        "scope": "self",
        "filters": {"by_user_id": "u1"},
        "columns": ["full_name"],
        "values": None,
        "needs_confirmation": False,
        "confidence": 0.9,
        "errors": [],
    }
    base.update(overrides)
    return DBAction(**base)


def test_staff_update_self_allowed() -> None:
    action = _action(
        crud="update",
        table="users",
        role="staff",
        scope="self",
        values={"phone": "+14155550101"},
        columns=None,
    )
    result = validate_db_action(action=action, actor_user_id="u1", actor_role="staff")
    assert result.allowed is True


def test_staff_update_other_denied() -> None:
    action = _action(
        crud="update",
        table="users",
        role="staff",
        scope="self",
        filters={"by_user_id": "u2"},
        values={"phone": "+14155550101"},
        columns=None,
    )
    result = validate_db_action(action=action, actor_user_id="u1", actor_role="staff")
    assert result.allowed is False
    assert "self_scope_required" in result.reasons


def test_staff_cannot_read_restaurants() -> None:
    action = _action(
        crud="read",
        table="restaurants",
        role="staff",
        scope="owned_restaurant",
        filters={"by_restaurant_id": "r1"},
        columns=["name"],
    )
    result = validate_db_action(action=action, actor_user_id="u1", actor_role="staff")
    assert result.allowed is False
    assert "table_not_allowed_for_role" in result.reasons


def test_owner_cannot_update_users() -> None:
    action = _action(
        crud="update",
        table="users",
        role="owner",
        scope="self",
        filters={"by_user_id": "u1"},
        values={"phone": "+14155550101"},
        columns=None,
    )
    result = validate_db_action(action=action, actor_user_id="u1", actor_role="owner")
    assert result.allowed is False
    assert "crud_not_allowed_for_role" in result.reasons


def test_read_requires_columns_and_filters() -> None:
    action = _action(columns=None, filters=None)
    result = validate_db_action(action=action, actor_user_id="u1", actor_role="staff")
    assert result.allowed is False
    assert "columns_required" in result.reasons
    assert "filters_required" in result.reasons


def test_normalize_self_scope_injects_user_filter() -> None:
    action = _action(filters=None)
    normalized = normalize_db_action(
        action=action, actor_user_id="u1", actor_role="staff"
    )
    assert normalized.normalized is not None
    assert normalized.normalized.filters is not None
    assert normalized.normalized.filters.get("by_user_id") == "u1"


def test_normalize_restaurant_scope_autofill() -> None:
    action = _action(
        table="restaurants",
        role="owner",
        scope="owned_restaurant",
        filters=None,
        columns=["name"],
    )
    normalized = normalize_db_action(
        action=action,
        actor_user_id="u1",
        actor_role="owner",
        restaurant_roles={"r1": "owner"},
    )
    assert normalized.normalized is not None
    assert normalized.normalized.filters is not None
    assert normalized.normalized.filters.get("by_restaurant_id") == "r1"
    assert "restaurant_scope_required" not in normalized.reasons


def test_normalize_restaurant_scope_requires_filter_when_multiple() -> None:
    action = _action(
        table="restaurants",
        role="owner",
        scope="owned_restaurant",
        filters=None,
        columns=["name"],
    )
    normalized = normalize_db_action(
        action=action,
        actor_user_id="u1",
        actor_role="owner",
        restaurant_roles={"r1": "owner", "r2": "owner"},
    )
    assert "restaurant_scope_required" in normalized.reasons


def test_normalize_strips_disallowed_columns() -> None:
    action = _action(
        table="restaurants",
        role="owner",
        scope="owned_restaurant",
        filters={"by_restaurant_id": "r1"},
        columns=["name", "bad"],
    )
    normalized = normalize_db_action(
        action=action,
        actor_user_id="u1",
        actor_role="owner",
        restaurant_roles={"r1": "owner"},
    )
    assert normalized.normalized is not None
    assert normalized.normalized.columns == ["name"]
    assert "columns_not_allowed" not in normalized.reasons
