"""
Restaurant management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.domain.services.restaurant_service import RestaurantService

from .base import format_date, has_restaurant_access, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_restaurant_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,  # For future policy checks
    restaurant_roles: dict[str, str] | None = None,  # For future policy checks
) -> dict[str, Tool]:
    """Create restaurant management tools."""

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

        if not has_restaurant_access(db, user_id, restaurant_id):
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
                "created_at": format_date(restaurant.created_at),
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

        if not is_restaurant_owner(db, user_id, restaurant_id):
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

    return {
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
    }
