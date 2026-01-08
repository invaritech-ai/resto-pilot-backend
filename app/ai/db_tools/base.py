"""
Base utilities and main tool factory for database tools.

This module provides shared utilities and the main create_db_tools function
that assembles all tools from different modules.

Architecture:
- Tools = Capabilities (what operations can be performed)
- Policies = Filtering (who can perform those operations)

See docs/db-tools-patterns.md for:
- When to use direct checks vs policy checks
- Patterns for simple vs complex tools
- How to add new tools with proper permission handling
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.restaurant_user import RestaurantUser
from app.policies.db_allowlist import (
    DB_ALLOWLIST,
    ROLE_OWNER,
    SCOPE_RESTAURANT_MEMBER,
    SCOPE_RESTAURANT_OWNER,
)


def format_date(dt_value: dt.datetime | None) -> str | None:
    """Format a datetime as a readable date string (e.g., 'Jan 7, 2026')."""
    if dt_value is None:
        return None
    return dt_value.strftime("%b %d, %Y")


def is_restaurant_owner(
    db: Session, user_id: uuid.UUID, restaurant_id: uuid.UUID
) -> bool:
    """Check if user is owner of a specific restaurant."""
    membership = db.scalar(
        select(RestaurantUser).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == user_id,
            RestaurantUser.role == "owner",
            RestaurantUser.status == "active",
        )
    )
    return membership is not None


def has_restaurant_access(
    db: Session, user_id: uuid.UUID, restaurant_id: uuid.UUID
) -> bool:
    """Check if user has any access to a restaurant."""
    membership = db.scalar(
        select(RestaurantUser).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == user_id,
            RestaurantUser.status != "removed",
        )
    )
    return membership is not None


def check_policy_permission(
    *,
    table: str,
    crud: str,  # "read", "create", "update", "delete"
    actor_role: str,
    restaurant_id: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """
    Check if an action is allowed by policy allowlist.

    This provides a way for tools to cross-reference with the policy system.
    Use this for complex tables where you want centralized policy management.

    Example usage in a tool:
        allowed, error = check_policy_permission(
            table="inventory_items",
            crud="update",
            actor_role=actor_role,
            restaurant_id=str(restaurant_id),
            restaurant_roles=restaurant_roles,
        )
        if not allowed:
            return f"Error: {error}"

    Args:
        table: Table name (e.g., "restaurants", "restaurant_users", "inventory_items")
        crud: Operation type ("read", "create", "update", "delete")
        actor_role: User's role ("owner" or "staff")
        restaurant_id: Restaurant ID if operation is scoped to a restaurant
        restaurant_roles: Map of restaurant_id -> role for the user

    Returns:
        (allowed: bool, error_message: str | None)
        If allowed=False, error_message explains why (for user-facing errors)
    """
    restaurant_roles = restaurant_roles or {}

    # Get allowlist for this role
    role_allowlist = DB_ALLOWLIST.get(actor_role, {})
    table_allowlist = role_allowlist.get(table)

    if not table_allowlist:
        return False, f"Table '{table}' is not accessible for {actor_role} role"

    # Get CRUD operation allowlist
    action_allow = table_allowlist.get(crud)
    if not action_allow:
        return (
            False,
            f"{crud} operation on '{table}' is not allowed for {actor_role} role",
        )

    # Check scope if restaurant-scoped
    scope = action_allow.get("scope")
    if scope in {SCOPE_RESTAURANT_OWNER, SCOPE_RESTAURANT_MEMBER}:
        if not restaurant_id:
            return False, f"Restaurant ID required for {table} operations"

        user_role_for_restaurant = restaurant_roles.get(restaurant_id)
        if not user_role_for_restaurant:
            return False, f"You don't have access to restaurant {restaurant_id}"

        if scope == SCOPE_RESTAURANT_OWNER and user_role_for_restaurant != ROLE_OWNER:
            return False, f"Only restaurant owners can perform {crud} on {table}"

    return True, None


def create_db_tools(
    *,
    db: Session,
    user_id: uuid.UUID,
    actor_role: str,  # User's highest role (for policy checks)
    restaurant_roles: dict[str, str],  # Map of restaurant_id -> role
    chat_id: int | None = None,  # Telegram chat_id for file processing tools
    session_id: uuid.UUID | None = None,  # Session ID for file processing tools
) -> dict[str, Tool]:
    """
    Create intent-based database tools with user context.

    Each tool is named after user intent, not database operations.
    Permission checks happen inside each tool.

    Tools can use check_policy_permission() to cross-reference with the policy allowlist
    for centralized permission management, especially useful for complex tables.

    Note: products and product_aliases tools have been removed as they are not
    part of the simplified intent-driven bot requirements.
    """
    # Import tool factories from each module (inside function to avoid circular imports)
    from . import (
        file_processing,
        inventory,
        invites,
        profile,
        restaurants,
        staff,
        suppliers,
    )

    # Pass actor_role and restaurant_roles so tools can use policy checks if needed
    profile_tools = profile.create_profile_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    restaurant_tools = restaurants.create_restaurant_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    staff_tools = staff.create_staff_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    invite_tools = invites.create_invite_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    supplier_tools = suppliers.create_supplier_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    file_processing_tools = file_processing.create_file_processing_tools(
        db=db,
        user_id=user_id,
        actor_role=actor_role,
        restaurant_roles=restaurant_roles,
        chat_id=chat_id,
        session_id=session_id,
    )
    inventory_tools = inventory.create_inventory_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )

    # Combine all tools
    all_tools: dict[str, Tool] = {}
    all_tools.update(profile_tools)
    all_tools.update(restaurant_tools)
    all_tools.update(staff_tools)
    all_tools.update(invite_tools)
    all_tools.update(supplier_tools)
    all_tools.update(file_processing_tools)
    all_tools.update(inventory_tools)

    return all_tools
