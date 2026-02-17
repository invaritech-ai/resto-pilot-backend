"""
Profile management tools for users.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.user import User

logger = logging.getLogger(__name__)


def create_profile_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,  # For future policy checks
    restaurant_roles: dict[str, str] | None = None,  # For future policy checks
    user_message: str | None = None,
) -> dict[str, Tool]:
    """Create profile management tools."""

    def _should_clear(field: str, message: str | None) -> bool:
        if not message:
            return False
        text = message.lower()
        if not any(keyword in text for keyword in ("clear", "remove", "delete", "reset", "erase")):
            return False
        if field == "name":
            return "name" in text
        if field == "phone":
            return "phone" in text or "number" in text
        if field == "username":
            return "username" in text or "user name" in text or "handle" in text
        return False

    def _resolve_field_update(value: Any, field: str) -> str | None:
        wants_clear = _should_clear(field, user_message)
        if value is None:
            return "" if wants_clear else None
        if isinstance(value, str) and not value.strip():
            return "" if wants_clear else None
        return str(value)

    def get_my_profile(args: dict[str, Any]) -> str:
        """Get the current user's profile information."""
        user = db.get(User, user_id)
        if not user:
            return "Error: User not found."

        parts = []
        if user.full_name:
            parts.append(f"Name: {user.full_name}")
        else:
            parts.append("Name: Not set")

        if user.phone:
            parts.append(f"Phone: {user.phone}")
        else:
            parts.append("Phone: Not set")

        if user.username:
            parts.append(f"Username: @{user.username}")

        return "\n".join(parts) if parts else "Your profile is empty."

    def update_my_profile(args: dict[str, Any]) -> str:
        """Update the current user's profile fields."""
        user = db.get(User, user_id)
        if not user:
            return "Error: User not found."

        full_name = _resolve_field_update(args.get("full_name"), "name")
        phone = _resolve_field_update(args.get("phone"), "phone")
        username = _resolve_field_update(args.get("username"), "username")

        if full_name is None and phone is None and username is None:
            return "Error: Provide at least one field to update (full_name, phone, or username)."

        updates = []
        if full_name is not None:
            user.full_name = full_name.strip() if full_name else None
            updates.append(f"Name: {user.full_name or 'cleared'}")

        if phone is not None:
            user.phone = phone.strip() if phone else None
            updates.append(f"Phone: {user.phone or 'cleared'}")

        if username is not None:
            user.username = username.strip().lstrip("@") if username else None
            updates.append(
                f"Username: @{user.username}" if user.username else "Username: cleared"
            )

        try:
            db.commit()
            return "Profile updated:\n" + "\n".join(updates)
        except Exception as e:
            db.rollback()
            logger.exception("update_my_profile_failed")
            return f"Error updating profile: {str(e)}"

    return {
        "get_my_profile": Tool(
            name="get_my_profile",
            description="Get your profile information (name, phone, username).",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=get_my_profile,
        ),
        "update_my_profile": Tool(
            name="update_my_profile",
            description="Update your profile information. Only include fields the user explicitly provided. Use empty string only when the user asks to clear a field.",
            parameters={
                "type": "object",
                "properties": {
                    "full_name": {
                        "type": "string",
                        "description": "Your full name. Pass empty string to clear.",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Your phone number. Pass empty string to clear.",
                    },
                    "username": {
                        "type": "string",
                        "description": "Your username (without @). Pass empty string to clear.",
                    },
                },
                "additionalProperties": False,
            },
            handler=update_my_profile,
        ),
    }
