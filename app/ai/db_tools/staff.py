"""
Staff management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.user import User
from app.domain.services.restaurant_service import RestaurantService

from .base import format_date, has_restaurant_access, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_staff_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,  # For future policy checks
    restaurant_roles: dict[str, str] | None = None,  # For future policy checks
) -> dict[str, Tool]:
    """Create staff management tools."""

    def list_staff(args: dict[str, Any]) -> str:
        """List all staff members of a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        service = RestaurantService(db)
        members = service.list_members(restaurant_id=restaurant_id)

        if not members:
            return "No staff members found."

        result = []
        for user_obj, membership in members:
            result.append(
                {
                    "user_id": str(user_obj.id),
                    "name": user_obj.full_name or "Unknown",
                    "username": f"@{user_obj.username}" if user_obj.username else None,
                    "role": membership.role,
                    "status": membership.status,
                    "joined_at": format_date(membership.joined_at),
                }
            )

        return json.dumps(result, indent=2)

    def revoke_staff_access(args: dict[str, Any]) -> str:
        """Remove a user from restaurant staff. Only owners can do this."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        target_user_id_str = args.get("user_id", "").strip()

        if not restaurant_id_str:
            return "Error: restaurant_id is required."
        if not target_user_id_str:
            return "Error: user_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        try:
            target_user_id = uuid.UUID(target_user_id_str)
        except ValueError:
            return "Error: Invalid user_id format."

        if not is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can revoke staff access."

        if target_user_id == user_id:
            return "Error: You cannot remove yourself. Transfer ownership first or delete the restaurant."

        from sqlalchemy import select
        from app.db.models.restaurant_user import RestaurantUser

        membership = db.scalar(
            select(RestaurantUser).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == target_user_id,
                RestaurantUser.status != "removed",
            )
        )

        if not membership:
            return "Error: User is not a member of this restaurant."

        try:
            membership.status = "removed"
            db.commit()

            target_user = db.get(User, target_user_id)
            name = target_user.full_name if target_user else "User"
            return f"Access revoked for {name}."
        except Exception as e:
            db.rollback()
            logger.exception("revoke_staff_access_failed")
            return f"Error revoking access: {str(e)}"

    return {
        "list_staff": Tool(
            name="list_staff",
            description="List all staff members of a restaurant.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                },
                "required": ["restaurant_id"],
                "additionalProperties": False,
            },
            handler=list_staff,
        ),
        "revoke_staff_access": Tool(
            name="revoke_staff_access",
            description="Remove a user from restaurant staff. Only owners can do this.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "The user's UUID to remove.",
                    },
                },
                "required": ["restaurant_id", "user_id"],
                "additionalProperties": False,
            },
            handler=revoke_staff_access,
        ),
    }
