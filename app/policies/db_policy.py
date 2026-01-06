from __future__ import annotations

from dataclasses import dataclass, field
import logging

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

logger = logging.getLogger(__name__)


@dataclass
class DBPolicyResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    normalized: DBAction | None = None


@dataclass
class DBNormalizationResult:
    normalized: DBAction | None = None
    reasons: list[str] = field(default_factory=list)


def _get_role_for_restaurant(
    *,
    restaurant_id: str | None,
    restaurant_roles: dict[str, str],
) -> str | None:
    """Get the role for a specific restaurant, or None if not a member."""
    if restaurant_id is None:
        return None
    return restaurant_roles.get(restaurant_id)


def _get_highest_role(*, restaurant_roles: dict[str, str]) -> str:
    """Get the highest role across all restaurants (owner > staff)."""
    if not restaurant_roles:
        return ROLE_STAFF
    if ROLE_OWNER in restaurant_roles.values():
        return ROLE_OWNER
    return ROLE_STAFF


def normalize_db_action(
    *,
    action: DBAction,
    actor_user_id: str,
    actor_role: str,  # Highest role (for allowlist lookup)
    restaurant_roles: dict[str, str] | None = None,  # Per-restaurant roles
) -> DBNormalizationResult:
    reasons: list[str] = []
    restaurant_roles = restaurant_roles or {}

    role_allowlist = DB_ALLOWLIST.get(actor_role, {})
    table_allowlist = role_allowlist.get(action.table)
    if not table_allowlist:
        reasons.append("table_not_allowed_for_role")
        logger.info(
            "db_action_normalize_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBNormalizationResult(None, reasons)

    action_allow = table_allowlist.get(action.crud)
    if not action_allow:
        reasons.append("crud_not_allowed_for_role")
        logger.info(
            "db_action_normalize_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBNormalizationResult(None, reasons)

    columns = action_allow.get("columns")
    allowed_columns = set(columns) if isinstance(columns, list) else set()
    scope = action_allow.get("scope")

    normalized = action.model_copy(deep=True)
    normalized.role = actor_role
    normalized.scope = str(scope)

    if normalized.columns is not None:
        normalized.columns = [col for col in normalized.columns if col in allowed_columns]
        if not normalized.columns:
            reasons.append("columns_not_allowed")

    if normalized.values is not None:
        normalized.values = {
            key: value for key, value in normalized.values.items() if key in allowed_columns
        }
        if not normalized.values:
            reasons.append("columns_not_allowed")

    filters = dict(normalized.filters or {})
    if scope == SCOPE_SELF:
        filters["by_user_id"] = actor_user_id
    elif scope in {SCOPE_OWNED_RESTAURANT, SCOPE_RESTAURANT_OWNER}:
        restaurant_id = filters.get("by_restaurant_id")
        roles = restaurant_roles or {}
        if not isinstance(restaurant_id, str):
            owned_ids = [rid for rid, role in roles.items() if role == ROLE_OWNER]
            if len(owned_ids) == 1:
                filters["by_restaurant_id"] = owned_ids[0]
                restaurant_id = owned_ids[0]
            else:
                reasons.append("restaurant_scope_required")
        else:
            if restaurant_id not in roles:
                reasons.append("restaurant_scope_required")
            elif scope == SCOPE_RESTAURANT_OWNER and roles.get(restaurant_id) != ROLE_OWNER:
                reasons.append("owner_role_required_for_restaurant")
    else:
        reasons.append("scope_not_supported")

    normalized.filters = filters or None

    if reasons:
        logger.info(
            "db_action_normalize_partial action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
    return DBNormalizationResult(normalized, reasons)


def validate_db_action(
    *,
    action: DBAction,
    actor_user_id: str,
    actor_role: str,  # Highest role (for allowlist lookup)
    restaurant_roles: dict[str, str] | None = None,  # Per-restaurant roles
) -> DBPolicyResult:
    reasons: list[str] = []
    restaurant_roles = restaurant_roles or {}

    if action.table in BUSINESS_TABLES_DENYLIST:
        reasons.append("table_not_exposed")
        logger.info(
            "db_action_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBPolicyResult(False, reasons)

    if actor_role not in {ROLE_OWNER, ROLE_STAFF}:
        reasons.append("actor_role_invalid")
        logger.info(
            "db_action_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBPolicyResult(False, reasons)

    role_allowlist = DB_ALLOWLIST.get(actor_role, {})
    table_allowlist = role_allowlist.get(action.table)
    if not table_allowlist:
        reasons.append("table_not_allowed_for_role")
        logger.info(
            "db_action_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBPolicyResult(False, reasons)

    action_allow = table_allowlist.get(action.crud)
    if not action_allow:
        reasons.append("crud_not_allowed_for_role")
        logger.info(
            "db_action_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
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
        restaurant_id = (action.filters or {}).get("by_restaurant_id")
        role_for_restaurant = _get_role_for_restaurant(
            restaurant_id=restaurant_id,
            restaurant_roles=restaurant_roles or {},
        )
        if role_for_restaurant is None:
            reasons.append("restaurant_scope_required")
        elif scope == SCOPE_RESTAURANT_OWNER and role_for_restaurant != ROLE_OWNER:
            reasons.append("owner_role_required_for_restaurant")
    else:
        reasons.append("scope_not_supported")

    if reasons:
        logger.info(
            "db_action_denied action_id=%s table=%s crud=%s role=%s reasons=%s",
            action.action_id,
            action.table,
            action.crud,
            actor_role,
            reasons,
        )
        return DBPolicyResult(False, reasons)

    normalized = action.model_copy()
    normalized.role = actor_role
    normalized.scope = str(scope)
    logger.info(
        "db_action_allowed action_id=%s table=%s crud=%s role=%s",
        action.action_id,
        action.table,
        action.crud,
        actor_role,
    )
    return DBPolicyResult(True, [], normalized)


def _is_self_scope(*, action: DBAction, actor_user_id: str) -> bool:
    if not action.filters:
        return False
    return action.filters.get("by_user_id") == actor_user_id


def _is_restaurant_scope(*, restaurant_id: str | None, restaurant_roles: dict[str, str]) -> bool:
    if not isinstance(restaurant_id, str):
        return False
    return restaurant_id in restaurant_roles
