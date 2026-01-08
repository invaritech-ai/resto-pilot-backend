"""
Base utilities and main tool factory for database tools.

This module provides shared utilities and the main create_db_tools function
that assembles all tools from different modules.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.restaurant_user import RestaurantUser


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


def create_db_tools(
    *,
    db: Session,
    user_id: uuid.UUID,
    actor_role: str,  # User's highest role (for permission checks)
    restaurant_roles: dict[str, str],  # Map of restaurant_id -> role
    chat_id: int | None = None,  # Telegram chat_id for file processing tools
    session_id: uuid.UUID | None = None,  # Session ID for file processing tools
) -> dict[str, Tool]:
    """
    Create intent-based database tools with user context.

    Each tool is named after user intent, not database operations.
    Permission checks happen inside each tool.
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

    # Pass actor_role and restaurant_roles so tools can use permission checks if needed
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
