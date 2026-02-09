"""
User context management for conversation.

Simplified version - just tracks active restaurant/supplier IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """User's current context - tracks active entities and pending actions."""

    active_restaurant_id: str | None = None
    active_supplier_id: str | None = None
    pending_action: dict[str, Any] | None = None
    last_list: dict[str, Any] | None = None
    last_file: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "active_restaurant_id": self.active_restaurant_id,
            "active_supplier_id": self.active_supplier_id,
        }
        if self.pending_action:
            result["pending_action"] = self.pending_action
        if self.last_list:
            result["last_list"] = self.last_list
        if self.last_file:
            result["last_file"] = self.last_file
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UserContext":
        if not data:
            return cls()
        return cls(
            active_restaurant_id=data.get("active_restaurant_id"),
            active_supplier_id=data.get("active_supplier_id"),
            pending_action=data.get("pending_action"),
            last_list=data.get("last_list"),
            last_file=data.get("last_file"),
        )


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
            pending_action=context_update.get("pending_action"),
        )
        save_context(db, user, new_context)
        return new_context

    # Update specific fields
    if "active_restaurant_id" in context_update:
        context.active_restaurant_id = context_update["active_restaurant_id"]
    if "active_supplier_id" in context_update:
        context.active_supplier_id = context_update["active_supplier_id"]
    if "pending_action" in context_update:
        context.pending_action = context_update["pending_action"]
    if context_update.get("clear_pending_action"):
        context.pending_action = None
    if "last_list" in context_update:
        context.last_list = context_update["last_list"]
    if context_update.get("clear_last_list"):
        context.last_list = None
    if "last_file" in context_update:
        context.last_file = context_update["last_file"]
    if context_update.get("clear_last_file"):
        context.last_file = None

    save_context(db, user, context)
    return context
