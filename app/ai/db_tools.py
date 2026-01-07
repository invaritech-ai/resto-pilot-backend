"""
Intent-based database tools for the AI agent.

Each tool maps directly to a user action and handles its own permission checks.
No complex policy layers - just clear, focused operations.
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
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService

logger = logging.getLogger(__name__)


def _is_restaurant_owner(
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


def _has_restaurant_access(
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
    actor_role: str,  # kept for compatibility, but not used for blocking
    restaurant_roles: dict[str, str],  # kept for compatibility
) -> dict[str, Tool]:
    """
    Create intent-based database tools with user context.

    Each tool is named after user intent, not database operations.
    Permission checks happen inside each tool.
    """

    # =========================================================================
    # PROFILE TOOLS
    # =========================================================================

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

    # =========================================================================
    # RESTAURANT TOOLS
    # =========================================================================

    def list_my_restaurants(args: dict[str, Any]) -> str:
        """List all restaurants the user owns or has access to."""
        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)

        if not rows:
            return "You don't have any restaurants yet. Use create_restaurant to create one."

        result = []
        for restaurant, membership in rows:
            result.append(
                {
                    "id": str(restaurant.id),
                    "name": restaurant.name,
                    "code": restaurant.restaurant_code,
                    "your_role": membership.role,
                }
            )

        return json.dumps(result, indent=2)

    def find_restaurant_by_name(args: dict[str, Any]) -> str:
        """Search for a restaurant by name among user's restaurants."""
        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required."

        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)

        if not rows:
            return "You don't have any restaurants. Create one with create_restaurant."

        name_lower = name.lower()
        matches = []
        for restaurant, membership in rows:
            if (
                name_lower in restaurant.name.lower()
                or restaurant.name.lower() in name_lower
            ):
                matches.append(
                    {
                        "id": str(restaurant.id),
                        "name": restaurant.name,
                        "code": restaurant.restaurant_code,
                        "your_role": membership.role,
                    }
                )

        if not matches:
            return f"No restaurant found matching '{name}'. Use list_my_restaurants to see all your restaurants."

        return json.dumps(matches, indent=2)

    def create_restaurant(args: dict[str, Any]) -> str:
        """Create a new restaurant. Any user can create restaurants."""
        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required."

        if len(name) < 2:
            return "Error: Restaurant name must be at least 2 characters."

        if len(name) > 100:
            return "Error: Restaurant name must be under 100 characters."

        # Check for duplicate names (case-insensitive)
        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)
        for restaurant, _ in rows:
            if name.lower() == restaurant.name.lower():
                return f"You already have a restaurant named '{restaurant.name}' (ID: {restaurant.id})."

        try:
            restaurant = service.create_restaurant(
                owner_user_id=user_id,
                name=name,
            )
            return json.dumps(
                {
                    "status": "created",
                    "id": str(restaurant.id),
                    "name": restaurant.name,
                    "code": restaurant.restaurant_code,
                    "message": f"Restaurant '{restaurant.name}' created! You are the owner.",
                },
                indent=2,
            )
        except Exception as e:
            logger.exception("create_restaurant_failed", extra={"name": name})
            return f"Error creating restaurant: {str(e)}"

    def get_restaurant(args: dict[str, Any]) -> str:
        """Get details of a specific restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not _has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        restaurant = db.get(Restaurant, restaurant_id)
        if not restaurant:
            return "Error: Restaurant not found."

        membership = db.scalar(
            select(RestaurantUser).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user_id,
                RestaurantUser.status != "removed",
            )
        )

        return json.dumps(
            {
                "id": str(restaurant.id),
                "name": restaurant.name,
                "code": restaurant.restaurant_code,
                "your_role": membership.role if membership else "none",
                "created_at": restaurant.created_at.isoformat()
                if restaurant.created_at
                else None,
            },
            indent=2,
        )

    def update_restaurant(args: dict[str, Any]) -> str:
        """Update restaurant details. Only owners can update."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not _is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can update restaurant details."

        restaurant = db.get(Restaurant, restaurant_id)
        if not restaurant:
            return "Error: Restaurant not found."

        name = args.get("name")
        if name is None:
            return "Error: Provide a field to update (name)."

        updates = []
        if name is not None:
            name = name.strip()
            if len(name) < 2:
                return "Error: Restaurant name must be at least 2 characters."
            if len(name) > 100:
                return "Error: Restaurant name must be under 100 characters."
            restaurant.name = name
            updates.append(f"Name: {name}")

        try:
            db.commit()
            return "Restaurant updated:\n" + "\n".join(updates)
        except Exception as e:
            db.rollback()
            logger.exception("update_restaurant_failed")
            return f"Error updating restaurant: {str(e)}"

    # =========================================================================
    # STAFF MANAGEMENT TOOLS
    # =========================================================================

    def list_staff(args: dict[str, Any]) -> str:
        """List all staff members of a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not _has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        service = RestaurantService(db)
        members = service.list_members(restaurant_id=restaurant_id)

        if not members:
            return "No staff members found."

        result = []
        for user_obj, membership in members:
            joined_at_str = None
            if membership.joined_at:
                # Format as readable date: "Jan 7, 2026"
                joined_at_str = membership.joined_at.strftime("%b %d, %Y")
            result.append(
                {
                    "user_id": str(user_obj.id),
                    "name": user_obj.full_name or "Unknown",
                    "username": f"@{user_obj.username}" if user_obj.username else None,
                    "role": membership.role,
                    "status": membership.status,
                    "joined_at": joined_at_str,
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

        if not _is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can revoke staff access."

        if target_user_id == user_id:
            return "Error: You cannot remove yourself. Transfer ownership first or delete the restaurant."

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

    # =========================================================================
    # INVITE CODE TOOLS
    # =========================================================================

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

        if not _is_restaurant_owner(db, user_id, restaurant_id):
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
                    "expires_at": invite.expires_at.isoformat()
                    if invite.expires_at
                    else None,
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

        if not _is_restaurant_owner(db, user_id, restaurant_id):
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
                        "expires_at": invite.expires_at.isoformat()
                        if invite.expires_at
                        else "Never",
                        "created_at": invite.created_at.isoformat()
                        if invite.created_at
                        else None,
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

        if not _is_restaurant_owner(db, user_id, invite.restaurant_id):
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

    # =========================================================================
    # RETURN ALL TOOLS
    # =========================================================================

    return {
        # Profile tools
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
        # Restaurant tools
        "list_my_restaurants": Tool(
            name="list_my_restaurants",
            description="List all restaurants/outlets you own or have access to.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_my_restaurants,
        ),
        "find_restaurant_by_name": Tool(
            name="find_restaurant_by_name",
            description="Search for a restaurant by name. Use this when user mentions a restaurant name.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The restaurant name to search for.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=find_restaurant_by_name,
        ),
        "create_restaurant": Tool(
            name="create_restaurant",
            description="Create a new restaurant/outlet. You will become the owner.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the new restaurant.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=create_restaurant,
        ),
        "get_restaurant": Tool(
            name="get_restaurant",
            description="Get details of a specific restaurant by ID.",
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
            handler=get_restaurant,
        ),
        "update_restaurant": Tool(
            name="update_restaurant",
            description="Update restaurant details. Only owners can update.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the restaurant.",
                    },
                },
                "required": ["restaurant_id", "name"],
                "additionalProperties": False,
            },
            handler=update_restaurant,
        ),
        # Staff management tools
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
        # Invite code tools
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
