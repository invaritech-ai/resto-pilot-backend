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
) -> dict[str, Tool]:
    """Create profile management tools."""

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
            verified = " (verified)" if user.is_phone_verified else " (not verified)"
            parts.append(f"Phone: {user.phone}{verified}")
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

        full_name = args.get("full_name")
        phone = args.get("phone")
        username = args.get("username")

        if full_name is None and phone is None and username is None:
            return "Error: Provide at least one field to update (full_name, phone, or username)."

        updates = []
        if full_name is not None:
            user.full_name = full_name.strip() if full_name else None
            updates.append(f"Name: {user.full_name or 'cleared'}")

        if phone is not None:
            user.phone = phone.strip() if phone else None
            user.is_phone_verified = False  # Reset verification on phone change
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
            description="Update your profile information. You can update name, phone, or username.",
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
