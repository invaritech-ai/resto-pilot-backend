"""
User context management for multi-step conversation flows.

Handles loading, updating, and clearing conversation context stored in User.state_data.
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
        # Keep only active_restaurant_id if specified
        new_context = UserContext(
            active_restaurant_id=context_update.get(
                "active_restaurant_id",
                context.active_restaurant_id,
            )
        )
        save_context(db, user, new_context)
        return new_context

    # Update specific fields
    if "active_operation" in context_update:
        context.active_operation = context_update["active_operation"]
    if "pending_params" in context_update:
        context.pending_params = context_update["pending_params"]
    if "collected_params" in context_update:
        context.collected_params = context_update["collected_params"]
    if "active_restaurant_id" in context_update:
        context.active_restaurant_id = context_update["active_restaurant_id"]
    if "staging_id" in context_update:
        context.staging_id = context_update["staging_id"]

    save_context(db, user, context)
    return context


def get_context_for_classifier(context: UserContext) -> dict[str, Any] | None:
    """
    Format context for passing to intent classifier.

    Args:
        context: User's current context

    Returns:
        Dict to pass to classifier, or None if no active operation
    """
    if not context.active_operation:
        return None

    return {
        "active_operation": context.active_operation,
        "pending_params": context.pending_params,
        "collected_params": context.collected_params,
    }
