"""
Invite code management tools.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.core.config import get_settings
from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.domain.services.invite_service import InviteCodeService

from .base import format_date, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_invite_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,  # For future policy checks
    restaurant_roles: dict[str, str] | None = None,  # For future policy checks
) -> dict[str, Tool]:
    """Create invite code management tools."""

    def create_invite_code(args: dict[str, Any]) -> str:
        """Create an invite code/link for adding staff to a restaurant. Only owners can create invites."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        target_role = args.get("role", "staff").strip().lower()
        expires_in_days = args.get("expires_in_days", 30)

        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if target_role not in {"owner", "staff"}:
            return "Error: role must be 'owner' or 'staff'."

        if not is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can create invite codes."

        restaurant = db.get(Restaurant, restaurant_id)
        if not restaurant:
            return "Error: Restaurant not found."

        try:
            settings = get_settings()
            expires_at = None
            if expires_in_days:
                expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
                    days=int(expires_in_days)
                )

            invite = InviteCodeService(db).create_invite_code(
                restaurant_id=restaurant_id,
                target_role=target_role,
                created_by_user_id=user_id,
                created_by_is_superuser=False,
                expires_at=expires_at,
            )

            deep_link = InviteCodeService.deep_link(
                bot_username=settings.telegram_bot_username,
                code=invite.code,
            )

            return json.dumps(
                {
                    "status": "created",
                    "code": invite.code,
                    "role": invite.role,
                    "restaurant": restaurant.name,
                    "deep_link": deep_link,
                    "expires_at": format_date(invite.expires_at),
                    "message": f"Share this link to invite {target_role}: {deep_link}",
                },
                indent=2,
            )
        except Exception as e:
            logger.exception("create_invite_code_failed")
            return f"Error creating invite code: {str(e)}"

    def list_invite_codes(args: dict[str, Any]) -> str:
        """List active invite codes for a restaurant. Only owners can view."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can view invite codes."

        now = dt.datetime.now(dt.UTC)
        invites = db.scalars(
            select(InviteCodes).where(
                InviteCodes.restaurant_id == restaurant_id,
                InviteCodes.used_at.is_(None),
            )
        ).all()

        # Filter out expired codes
        active_invites = []
        for invite in invites:
            if invite.expires_at is None or invite.expires_at > now:
                settings = get_settings()
                deep_link = InviteCodeService.deep_link(
                    bot_username=settings.telegram_bot_username,
                    code=invite.code,
                )
                active_invites.append(
                    {
                        "code": invite.code,
                        "role": invite.role,
                        "deep_link": deep_link,
                        "expires_at": format_date(invite.expires_at) or "Never",
                        "created_at": format_date(invite.created_at),
                    }
                )

        if not active_invites:
            return "No active invite codes. Use create_invite_code to create one."

        return json.dumps(active_invites, indent=2)

    def delete_invite_code(args: dict[str, Any]) -> str:
        """Delete/revoke an invite code. Only owners can delete."""
        code = args.get("code", "").strip().upper()
        if not code:
            return "Error: code is required."

        invite = db.scalar(select(InviteCodes).where(InviteCodes.code == code))

        if not invite:
            return "Error: Invite code not found."

        if not is_restaurant_owner(db, user_id, invite.restaurant_id):
            return "Error: Only restaurant owners can delete invite codes."

        if invite.used_at:
            return (
                "Error: This invite code has already been used and cannot be deleted."
            )

        try:
            db.delete(invite)
            db.commit()
            return f"Invite code {code} has been deleted."
        except Exception as e:
            db.rollback()
            logger.exception("delete_invite_code_failed")
            return f"Error deleting invite code: {str(e)}"

    return {
        "create_invite_code": Tool(
            name="create_invite_code",
            description="Create an invite link to add staff to your restaurant. Only owners can create invites.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Role for the invitee: 'staff' or 'owner'. Defaults to 'staff'.",
                        "enum": ["staff", "owner"],
                    },
                    "expires_in_days": {
                        "type": "integer",
                        "description": "Days until the invite expires. Defaults to 30.",
                    },
                },
                "required": ["restaurant_id"],
                "additionalProperties": False,
            },
            handler=create_invite_code,
        ),
        "list_invite_codes": Tool(
            name="list_invite_codes",
            description="List active invite codes for a restaurant. Only owners can view.",
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
            handler=list_invite_codes,
        ),
        "delete_invite_code": Tool(
            name="delete_invite_code",
            description="Delete/revoke an unused invite code. Only owners can delete.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The invite code to delete.",
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=delete_invite_code,
        ),
    }
