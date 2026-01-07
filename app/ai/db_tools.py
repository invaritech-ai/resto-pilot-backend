from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai.db_schema import get_allowed_tables_for_role
from app.ai.tools import Tool
from app.core.config import get_settings
from app.db_engine.read_executor import execute_read_action
from app.db_engine.write_executor import stage_cud_action
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService
from app.policies.db_allowlist import ROLE_OWNER, SCOPE_OWNED_RESTAURANT
from app.policies.db_policy import normalize_db_action, validate_db_action
from app.schemas.db_action import DBAction

logger = logging.getLogger(__name__)


def _format_capabilities(
    actor_role: str, restaurant_roles: dict[str, str] | None = None
) -> str:
    """
    Format capabilities as a readable string.

    If restaurant_roles is provided, shows union of capabilities across all restaurants.
    Otherwise shows capabilities for the highest role only.
    """
    # Get capabilities for all roles the user has
    roles_seen = {actor_role}
    if restaurant_roles:
        roles_seen.update(restaurant_roles.values())

    # Build union of capabilities across all roles
    all_capabilities: dict[str, set[str]] = {}  # table_name -> set of operations

    for role in roles_seen:
        schema = get_allowed_tables_for_role(role=role)
        for table_name, table_info in schema.items():
            permissions = table_info.get("permissions", {})
            if permissions:
                if table_name not in all_capabilities:
                    all_capabilities[table_name] = set()
                all_capabilities[table_name].update(permissions.keys())

    # Build user-friendly descriptions
    table_descriptions = {
        "users": "view and update user profiles",
        "restaurants": "view and update restaurant/outlet information",
        "restaurant_users": "manage restaurant memberships and staff",
        "invite_codes": "view and manage invite codes",
    }

    capabilities = []
    for table_name, crud_ops in sorted(all_capabilities.items()):
        desc = table_descriptions.get(table_name, f"access {table_name} data")
        crud_list = sorted(crud_ops)

        if "read" in crud_list and "update" in crud_list:
            capabilities.append(f"- {desc}")
        elif "read" in crud_list:
            capabilities.append(
                f"- view {desc.replace('view ', '').replace('manage ', '').replace('access ', '')}"
            )
        elif "create" in crud_list:
            capabilities.append(
                f"- create {desc.replace('view and ', '').replace('manage ', '').replace('access ', '')}"
            )

    if not capabilities:
        return (
            f"You have '{actor_role}' role, but no specific capabilities are available."
        )

    # Show role context if user has multiple roles
    role_context = actor_role
    if restaurant_roles and len(set(restaurant_roles.values())) > 1:
        unique_roles = sorted(set(restaurant_roles.values()))
        role_context = f"{actor_role} (varies by restaurant: {', '.join(unique_roles)})"

    return f"You have '{role_context}' role. You can:\n" + "\n".join(capabilities)


def _format_restaurants(restaurants: list[tuple]) -> str:
    """Format restaurant list as JSON."""
    result = []
    for restaurant, membership in restaurants:
        result.append(
            {
                "id": str(restaurant.id),
                "name": restaurant.name,
                "restaurant_code": restaurant.restaurant_code,
                "role": membership.role,
            }
        )
    return json.dumps(result, indent=2) if result else "No restaurants found."


def create_db_tools(
    *,
    db: Session,
    user_id: uuid.UUID,
    actor_role: str,
    restaurant_roles: dict[str, str],
) -> dict[str, Tool]:
    """
    Create DB tools with user context injected via closures.

    Args:
        db: Database session
        user_id: Current user's ID
        actor_role: User's highest role (owner or staff)
        restaurant_roles: Map of restaurant_id -> role for per-restaurant checks

    Returns:
        Dictionary of tool name -> Tool
    """

    def get_my_capabilities(args: dict[str, Any]) -> str:
        """List all tables and CRUD operations available to the user."""
        return _format_capabilities(actor_role, restaurant_roles)

    def list_my_restaurants(args: dict[str, Any]) -> str:
        """List all restaurants the user has access to."""
        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)
        return _format_restaurants(rows)

    def find_restaurant_by_name(args: dict[str, Any]) -> str:
        """Search for a restaurant by name."""
        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required"

        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)

        # Fuzzy match on name
        matches = []
        name_lower = name.lower()
        for restaurant, membership in rows:
            if (
                name_lower in restaurant.name.lower()
                or restaurant.name.lower() in name_lower
            ):
                matches.append(
                    {
                        "id": str(restaurant.id),
                        "name": restaurant.name,
                        "restaurant_code": restaurant.restaurant_code,
                        "role": membership.role,
                    }
                )

        if not matches:
            return f"No restaurant found matching '{name}'. Use create_restaurant to create a new one."
        return json.dumps(matches, indent=2)

    def create_restaurant(args: dict[str, Any]) -> str:
        """Create a new restaurant. Users with no restaurants can create their first one."""
        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required"

        # Allow users with no restaurants to create their first one
        # They will automatically become owner when the restaurant is created
        if actor_role != ROLE_OWNER and restaurant_roles:
            return "Error: Only restaurant owners can create additional restaurants. If you're a staff member, ask your restaurant owner to create it."

        # Check if restaurant with this name already exists
        service = RestaurantService(db)
        rows = service.list_for_user(user_id=user_id)
        for restaurant, _ in rows:
            if name.lower() == restaurant.name.lower():
                return f"Restaurant '{restaurant.name}' already exists (ID: {restaurant.id})."

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
                    "restaurant_code": restaurant.restaurant_code,
                }
            )
        except Exception as e:
            logger.exception("create_restaurant_failed", extra={"name": name})
            return f"Error creating restaurant: {str(e)}"

    def read_records(args: dict[str, Any]) -> str:
        """Read records from a table with policy enforcement."""
        table = args.get("table", "").strip()
        columns = args.get("columns", [])
        restaurant_id = args.get("restaurant_id")

        if not table:
            return "Error: table is required"
        if not columns:
            return "Error: columns is required (list of column names to read)"

        # Build filters based on scope
        filters: dict[str, Any] = {}
        if restaurant_id:
            filters["by_restaurant_id"] = restaurant_id

        action = DBAction(
            action_id=str(uuid.uuid4()),
            intent=f"Read {table}",
            crud="read",
            table=table,
            role=actor_role,
            scope=SCOPE_OWNED_RESTAURANT,
            columns=columns,
            filters=filters if filters else None,
        )

        # Normalize and validate
        normalized_result = normalize_db_action(
            action=action,
            actor_user_id=str(user_id),
            actor_role=actor_role,
            restaurant_roles=restaurant_roles,
        )

        if normalized_result.normalized is None:
            return f"Error: {', '.join(normalized_result.reasons)}"

        validation_result = validate_db_action(
            action=normalized_result.normalized,
            actor_user_id=str(user_id),
            actor_role=actor_role,
            restaurant_roles=restaurant_roles,
        )

        if not validation_result.allowed:
            return f"Error: {', '.join(validation_result.reasons)}"

        try:
            results = execute_read_action(
                session=db, action=normalized_result.normalized
            )
            if not results:
                return "No records found."
            # Convert UUIDs and datetimes to strings for JSON serialization
            for row in results:
                for k, v in row.items():
                    if isinstance(v, uuid.UUID):
                        row[k] = str(v)
                    elif hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
            # Format results naturally - don't return raw JSON
            if len(results) == 1:
                result = results[0]
                # For user profile reads, format naturally
                if table == "users":
                    parts = []
                    if "full_name" in result and result["full_name"]:
                        parts.append(f"Name: {result['full_name']}")
                    if "phone" in result:
                        if result["phone"]:
                            parts.append(f"Phone: {result['phone']}")
                        else:
                            parts.append("Phone: Not set")
                    if "username" in result and result["username"]:
                        parts.append(f"Username: {result['username']}")
                    if not parts:
                        return (
                            "Your profile is mostly empty. You can update it if needed."
                        )
                    return "\n".join(parts)

            # For other tables or multiple results, return structured but readable format
            return json.dumps(results, indent=2)
        except Exception as e:
            logger.exception("read_records_failed", extra={"table": table})
            return f"Error reading records: {str(e)}"

    def stage_write_action(args: dict[str, Any]) -> str:
        """Stage a create/update/delete action for user confirmation."""
        crud = args.get("crud", "").strip()
        table = args.get("table", "").strip()
        values = args.get("values", {})
        filters = args.get("filters", {})

        if not crud or crud not in {"create", "update", "delete"}:
            return "Error: crud must be one of: create, update, delete"
        if not table:
            return "Error: table is required"
        if crud in {"create", "update"} and not values:
            return "Error: values is required for create/update"
        if crud in {"update", "delete"} and not filters:
            return "Error: filters is required for update/delete"

        action = DBAction(
            action_id=str(uuid.uuid4()),
            intent=f"{crud} {table}",
            crud=crud,
            table=table,
            role=actor_role,
            scope=SCOPE_OWNED_RESTAURANT,
            values=values if values else None,
            filters=filters if filters else None,
            needs_confirmation=True,
        )

        # Normalize and validate
        normalized_result = normalize_db_action(
            action=action,
            actor_user_id=str(user_id),
            actor_role=actor_role,
            restaurant_roles=restaurant_roles,
        )

        if normalized_result.normalized is None:
            return f"Error: {', '.join(normalized_result.reasons)}"

        validation_result = validate_db_action(
            action=normalized_result.normalized,
            actor_user_id=str(user_id),
            actor_role=actor_role,
            restaurant_roles=restaurant_roles,
        )

        if not validation_result.allowed:
            return f"Error: {', '.join(validation_result.reasons)}"

        try:
            pending = stage_cud_action(
                session=db,
                user_id=user_id,
                action=normalized_result.normalized,
                chat_id=None,  # Will be set by caller if needed
                session_id=None,  # Will be set by caller if needed
            )
            db.commit()
            return json.dumps(
                {
                    "status": "staged",
                    "pending_action_id": str(pending.id),
                    "message": "Action staged. User must reply /confirm to proceed or /cancel to abort.",
                }
            )
        except Exception as e:
            db.rollback()
            logger.exception(
                "stage_write_action_failed", extra={"crud": crud, "table": table}
            )
            return f"Error staging action: {str(e)}"

    def create_invite_code(args: dict[str, Any]) -> str:
        """Create an invite code/link for adding staff to your restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        target_role = args.get("role", "staff").strip().lower()
        expires_in_days = args.get("expires_in_days", 30)

        if not restaurant_id_str:
            return "Error: restaurant_id is required"

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: invalid restaurant_id format"

        if target_role not in {"owner", "staff"}:
            return "Error: role must be 'owner' or 'staff'"

        # Check if user is owner of this restaurant
        if restaurant_id_str not in restaurant_roles:
            return f"Error: You don't have access to restaurant {restaurant_id_str}"
        if restaurant_roles[restaurant_id_str] != ROLE_OWNER:
            return "Error: Only restaurant owners can create invite codes"

        try:
            settings = get_settings()
            expires_at = None
            if expires_in_days:
                import datetime as dt

                expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
                    days=int(expires_in_days)
                )

            # Check if user is superuser (simplified - would need telegram_id lookup in practice)
            created_by_is_superuser = False
            invite = InviteCodeService(db).create_invite_code(
                restaurant_id=restaurant_id,
                target_role=target_role,
                created_by_user_id=user_id,
                created_by_is_superuser=created_by_is_superuser,
                expires_at=expires_at,
            )

            deep_link = InviteCodeService.deep_link(
                bot_username=settings.telegram_bot_username, code=invite.code
            )

            return json.dumps(
                {
                    "status": "created",
                    "code": invite.code,
                    "role": invite.role,
                    "deep_link": deep_link,
                    "expires_at": invite.expires_at.isoformat()
                    if invite.expires_at
                    else None,
                    "message": f"Invite code created! Share this link: {deep_link}",
                },
                indent=2,
            )
        except Exception as e:
            logger.exception(
                "create_invite_code_failed", extra={"restaurant_id": restaurant_id_str}
            )
            return f"Error creating invite code: {str(e)}"

    def revoke_staff_access(args: dict[str, Any]) -> str:
        """Revoke a user's access to your restaurant (remove them from staff)."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        user_id_str = args.get("user_id", "").strip()

        if not restaurant_id_str:
            return "Error: restaurant_id is required"
        if not user_id_str:
            return "Error: user_id is required"

        # Check if user is owner of this restaurant
        if restaurant_id_str not in restaurant_roles:
            return f"Error: You don't have access to restaurant {restaurant_id_str}"
        if restaurant_roles[restaurant_id_str] != ROLE_OWNER:
            return "Error: Only restaurant owners can revoke access"

        # Use stage_write_action to update restaurant_users status to "removed"
        return stage_write_action(
            {
                "crud": "update",
                "table": "restaurant_users",
                "values": {"status": "removed"},
                "filters": {
                    "by_restaurant_id": restaurant_id_str,
                    "by_user_id": user_id_str,
                },
            }
        )

    # Build and return tools dictionary
    return {
        "get_my_capabilities": Tool(
            name="get_my_capabilities",
            description="List all database tables and CRUD operations available to the current user based on their role.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=get_my_capabilities,
        ),
        "list_my_restaurants": Tool(
            name="list_my_restaurants",
            description="List all restaurants/outlets the user owns or has access to.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_my_restaurants,
        ),
        "find_restaurant_by_name": Tool(
            name="find_restaurant_by_name",
            description="Search for a restaurant/outlet by name. Use this when the user mentions an outlet name to check if it exists.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The restaurant/outlet name to search for.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=find_restaurant_by_name,
        ),
        "create_restaurant": Tool(
            name="create_restaurant",
            description="Create a new restaurant/outlet. Users with no restaurants can create their first one. Owners can create additional restaurants.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name for the new restaurant/outlet.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=create_restaurant,
        ),
        "read_records": Tool(
            name="read_records",
            description="Read records from a database table. Specify the table name and columns to read. Optionally filter by restaurant_id.",
            parameters={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Table name: users, restaurants, restaurant_users, or invite_codes.",
                        "enum": [
                            "users",
                            "restaurants",
                            "restaurant_users",
                            "invite_codes",
                        ],
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of column names to read.",
                    },
                    "restaurant_id": {
                        "type": "string",
                        "description": "Optional: filter by restaurant ID.",
                    },
                },
                "required": ["table", "columns"],
                "additionalProperties": False,
            },
            handler=read_records,
        ),
        "stage_write_action": Tool(
            name="stage_write_action",
            description="Stage a create/update/delete action for user confirmation. Use for general database operations. For invite codes, prefer create_invite_code. For removing staff, prefer revoke_staff_access. The user must reply /confirm or /cancel.",
            parameters={
                "type": "object",
                "properties": {
                    "crud": {
                        "type": "string",
                        "description": "The operation type.",
                        "enum": ["create", "update", "delete"],
                    },
                    "table": {
                        "type": "string",
                        "description": "Table name: users, restaurants, restaurant_users, or invite_codes.",
                        "enum": [
                            "users",
                            "restaurants",
                            "restaurant_users",
                            "invite_codes",
                        ],
                    },
                    "values": {
                        "type": "object",
                        "description": "For create/update: the column values to set.",
                    },
                    "filters": {
                        "type": "object",
                        "description": "For update/delete: filters to identify records (e.g., by_restaurant_id, by_user_id).",
                    },
                },
                "required": ["crud", "table"],
                "additionalProperties": False,
            },
            handler=stage_write_action,
        ),
    }
