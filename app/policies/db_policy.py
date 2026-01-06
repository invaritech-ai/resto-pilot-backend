from __future__ import annotations

from dataclasses import dataclass, field

from app.policies.db_allowlist import (
    BUSINESS_TABLES_DENYLIST,
    DB_ALLOWLIST,
    ROLE_OWNER,
    ROLE_STAFF,
    SCOPE_OWNED_RESTAURANT,
    SCOPE_RESTAURANT_OWNER,
    SCOPE_SELF,
)
from app.schemas.db_action import DBAction


@dataclass
class DBPolicyResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    normalized: DBAction | None = None


def validate_db_action(
    *,
    action: DBAction,
    actor_user_id: str,
    actor_role: str,
    actor_restaurant_ids: set[str] | None = None,
) -> DBPolicyResult:
    reasons: list[str] = []

    if action.table in BUSINESS_TABLES_DENYLIST:
        reasons.append("table_not_exposed")
        return DBPolicyResult(False, reasons)

    if actor_role not in {ROLE_OWNER, ROLE_STAFF}:
        reasons.append("actor_role_invalid")
        return DBPolicyResult(False, reasons)

    role_allowlist = DB_ALLOWLIST.get(actor_role, {})
    table_allowlist = role_allowlist.get(action.table)
    if not table_allowlist:
        reasons.append("table_not_allowed_for_role")
        return DBPolicyResult(False, reasons)

    action_allow = table_allowlist.get(action.crud)
    if not action_allow:
        reasons.append("crud_not_allowed_for_role")
        return DBPolicyResult(False, reasons)

    columns = action_allow.get("columns")
    allowed_columns = set(columns) if isinstance(columns, list) else set()
    scope = action_allow.get("scope")

    if action.crud == "read":
        if not action.columns:
            reasons.append("columns_required")
        else:
            requested = set(action.columns)
            if not requested.issubset(allowed_columns):
                reasons.append("columns_not_allowed")

    if action.crud in {"create", "update"}:
        if not action.values:
            reasons.append("values_required")
        else:
            requested = set(action.values.keys())
            if not requested.issubset(allowed_columns):
                reasons.append("columns_not_allowed")

    if action.crud in {"read", "update", "delete"}:
        if not action.filters:
            reasons.append("filters_required")

    if scope == SCOPE_SELF:
        if not _is_self_scope(action=action, actor_user_id=actor_user_id):
            reasons.append("self_scope_required")
    elif scope in {SCOPE_OWNED_RESTAURANT, SCOPE_RESTAURANT_OWNER}:
        if not _is_restaurant_scope(
            action=action,
            actor_restaurant_ids=actor_restaurant_ids,
        ):
            reasons.append("restaurant_scope_required")
    else:
        reasons.append("scope_not_supported")

    if reasons:
        return DBPolicyResult(False, reasons)

    normalized = action.model_copy()
    normalized.role = actor_role
    normalized.scope = str(scope)
    return DBPolicyResult(True, [], normalized)


def _is_self_scope(*, action: DBAction, actor_user_id: str) -> bool:
    if not action.filters:
        return False
    return action.filters.get("by_user_id") == actor_user_id


def _is_restaurant_scope(
    *,
    action: DBAction,
    actor_restaurant_ids: set[str] | None,
) -> bool:
    if not action.filters:
        return False
    if not actor_restaurant_ids:
        return False
    restaurant_id = action.filters.get("by_restaurant_id")
    if not isinstance(restaurant_id, str):
        return False
    return restaurant_id in actor_restaurant_ids
