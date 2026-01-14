"""
User context management for conversation.

Simplified version - just tracks active restaurant/supplier IDs.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.user import User
from app.conversation.executor import UserContext

logger = logging.getLogger(__name__)


def load_context(db: Session, user: User) -> UserContext:
    """
    Load user's conversation context from database.

    Args:
        db: Database session
        user: User model instance

    Returns:
        UserContext populated from user.state_data or empty context
    """
    state_data = user.state_data
    if not isinstance(state_data, dict):
        return UserContext()
    return UserContext.from_dict(state_data)


def save_context(
    db: Session,
    user: User,
    context: UserContext,
) -> None:
    """
    Save user's conversation context to database.

    Args:
        db: Database session
        user: User model instance
        context: UserContext to save
    """
    user.state_data = context.to_dict()
    db.add(user)
    # Don't commit - let caller manage transaction


def clear_context(db: Session, user: User) -> None:
    """
    Clear user's conversation context.

    Args:
        db: Database session
        user: User model instance
    """
    user.state_data = None
    db.add(user)
    # Don't commit - let caller manage transaction


def update_context_from_result(
    db: Session,
    user: User,
    context: UserContext,
    context_update: dict[str, Any],
) -> UserContext:
    """
    Update context based on execution result.

    Args:
        db: Database session
        user: User model instance
        context: Current context
        context_update: Updates from ExecutionResult

    Returns:
        Updated UserContext
    """
    if not context_update:
        return context

    # Check for clear flag
    if context_update.get("clear"):
        # Keep only active IDs if specified
        new_context = UserContext(
            active_restaurant_id=context_update.get(
                "active_restaurant_id",
                context.active_restaurant_id,
            ),
            active_supplier_id=context_update.get(
                "active_supplier_id",
                context.active_supplier_id,
            ),
        )
        save_context(db, user, new_context)
        return new_context

    # Update specific fields
    if "active_restaurant_id" in context_update:
        context.active_restaurant_id = context_update["active_restaurant_id"]
    if "active_supplier_id" in context_update:
        context.active_supplier_id = context_update["active_supplier_id"]

    save_context(db, user, context)
    return context
