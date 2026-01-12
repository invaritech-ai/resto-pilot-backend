"""
Intent executor that maps classified intents to database operations.

Reuses existing db_tools handlers where possible, wrapping them with
context-aware parameter handling and template responses.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent, ClassifiedIntent, get_missing_param_prompt
from app.conversation import responses
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.db.models.suppliers import Suppliers
from app.db.models.file_processing_staging import FileProcessingStaging
from app.domain.services.restaurant_service import RestaurantService
from app.domain.services.invite_service import InviteCodeService
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing an intent."""

    response: str
    success: bool = True
    # For multi-step flows
    needs_input: bool = False
    prompt: str | None = None
    # Context to persist
    context_update: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserContext:
    """User's current operation context."""

    active_operation: str | None = None
    pending_params: list[str] = field(default_factory=list)
    collected_params: dict[str, Any] = field(default_factory=dict)
    active_restaurant_id: str | None = None
    active_supplier_id: str | None = None
    staging_id: str | None = None  # For file processing

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_operation": self.active_operation,
            "pending_params": self.pending_params,
            "collected_params": self.collected_params,
            "active_restaurant_id": self.active_restaurant_id,
            "active_supplier_id": self.active_supplier_id,
            "staging_id": self.staging_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UserContext":
        if not data:
            return cls()
        return cls(
            active_operation=data.get("active_operation"),
            pending_params=data.get("pending_params", []),
            collected_params=data.get("collected_params", {}),
            active_restaurant_id=data.get("active_restaurant_id"),
            active_supplier_id=data.get("active_supplier_id"),
            staging_id=data.get("staging_id"),
        )


def _get_user_restaurants(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get list of restaurants user has access to."""
    service = RestaurantService(db)
    rows = service.list_for_user(user_id=user_id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "role": m.role,
        }
        for r, m in rows
    ]


def _match_by_name_or_number(
    search_value: str,
    items: list[dict[str, Any]],
    name_key: str = "name",
    id_key: str = "id",
) -> str | None:
    """
    Match an item by name, partial name, or list number.
    
    Args:
        search_value: User's input (name, partial name, or number like "1", "2")
        items: List of dicts with at least name_key and id_key
        name_key: Key to use for name matching
        id_key: Key to return as the matched ID
    
    Returns:
        The matched item's ID, or None if no match
    """
    if not search_value or not items:
        return None
    
    search_value = search_value.strip()
    
    # Try to match by number (1, 2, 3, etc) - 1-indexed
    if search_value.isdigit():
        idx = int(search_value) - 1
        if 0 <= idx < len(items):
            return items[idx][id_key]
    
    # Try exact name match (case-insensitive)
    search_lower = search_value.lower()
    for item in items:
        if item[name_key].lower() == search_lower:
            return item[id_key]
    
    # Try partial name match
    for item in items:
        if search_lower in item[name_key].lower():
            return item[id_key]
    
    return None


def _resolve_restaurant_id(
    db: Session,
    user_id: uuid.UUID,
    context: UserContext,
    params: dict[str, Any],
) -> tuple[str | None, str | None]:
    """
    Resolve restaurant_id from params, context, or auto-select if only one.
    Returns (restaurant_id, error_message).
    
    Handles multiple input formats:
    - Valid UUID string
    - Restaurant name (fuzzy match)
    - Number (1, 2, 3) referring to list position
    """
    restaurants = _get_user_restaurants(db, user_id)
    if not restaurants:
        return None, responses.ERROR_NO_OUTLETS

    # Check if provided in params
    param_value = params.get("restaurant_id", "").strip() if params.get("restaurant_id") else ""
    
    if param_value:
        # Try to parse as UUID first
        try:
            uuid.UUID(param_value)
            # Valid UUID - verify user has access
            if any(r["id"] == param_value for r in restaurants):
                return param_value, None
            else:
                return None, responses.OUTLET_NO_ACCESS
        except ValueError:
            pass
        
        # Try to match by name or number
        matched_id = _match_by_name_or_number(param_value, restaurants)
        if matched_id:
            return matched_id, None
        
        # No match found - will prompt for selection

    # Check context
    if context.active_restaurant_id:
        # Verify context restaurant is still accessible
        if any(r["id"] == context.active_restaurant_id for r in restaurants):
            return context.active_restaurant_id, None

    # Auto-select if user has only one restaurant
    if len(restaurants) == 1:
        return restaurants[0]["id"], None

    # Multiple restaurants - need to ask
    return None, None  # Will prompt for selection


def _is_restaurant_owner(db: Session, user_id: uuid.UUID, restaurant_id: uuid.UUID) -> bool:
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


def _resolve_supplier_id(
    db: Session,
    restaurant_id: str,
    param_value: str | None,
) -> str | None:
    """
    Resolve supplier_id from user input.
    
    Handles:
    - Valid UUID string
    - Supplier name (fuzzy match)
    - Number (1, 2, 3) referring to list position
    
    Returns supplier_id as string or None if not resolved.
    """
    if not param_value:
        return None
    
    param_value = param_value.strip()
    
    # Try to parse as UUID first
    try:
        uuid.UUID(param_value)
        return param_value
    except ValueError:
        pass
    
    # Load suppliers for this restaurant
    suppliers = db.scalars(
        select(Suppliers).where(
            Suppliers.restaurant_id == uuid.UUID(restaurant_id),
            Suppliers.is_active == True,
        ).order_by(Suppliers.name.asc())
    ).all()
    
    if not suppliers:
        return None
    
    # Convert to list of dicts for the helper
    supplier_list = [{"id": str(s.id), "name": s.name} for s in suppliers]
    return _match_by_name_or_number(param_value, supplier_list)


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    """Safely parse a UUID string, returning None if invalid."""
    if not value:
        return None
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError):
        return None


def execute_intent(
    *,
    classified: ClassifiedIntent,
    db: Session,
    user: User,
    context: UserContext,
    settings: Settings | None = None,
) -> ExecutionResult:
    """
    Execute a classified intent.

    Args:
        classified: The classified intent with params
        db: Database session
        user: Current user
        context: User's operation context
        settings: App settings (optional, will load if not provided)

    Returns:
        ExecutionResult with response text and context updates
    """
    settings = settings or get_settings()
    intent = classified.intent
    params = classified.params

    # Merge with context params for multi-step flows
    if context.active_operation == intent.value:
        params = {**context.collected_params, **params}

    # Navigation intents
    if intent == Intent.SHOW_MENU:
        return ExecutionResult(response=responses.MAIN_MENU)

    if intent == Intent.CANCEL:
        if context.active_operation:
            return ExecutionResult(
                response=responses.CANCEL_SUCCESS,
                context_update={"clear": True},
            )
        return ExecutionResult(response=responses.CANCEL_NOTHING)

    if intent == Intent.UNKNOWN:
        return ExecutionResult(response=responses.CANT_HELP, success=False)

    # Profile intents
    if intent == Intent.VIEW_PROFILE:
        return _execute_view_profile(user)

    if intent == Intent.UPDATE_NAME:
        return _execute_update_name(db, user, params, context)

    if intent == Intent.UPDATE_PHONE:
        return _execute_update_phone(db, user, params, context)

    if intent == Intent.UPLOAD_PRICE_LIST:
        return _execute_upload_price_list(db, user, params, context)

    # Outlet intents
    if intent == Intent.LIST_OUTLETS:
        return _execute_list_outlets(db, user)

    if intent == Intent.ADD_OUTLET:
        return _execute_add_outlet(db, user, params, context)

    if intent == Intent.UPDATE_OUTLET:
        return _execute_update_outlet(db, user, params, context)

    # Staff intents
    if intent == Intent.LIST_STAFF:
        return _execute_list_staff(db, user, params, context)

    if intent == Intent.ADD_STAFF:
        return _execute_add_staff(db, user, params, context, settings)

    if intent == Intent.REVOKE_STAFF:
        return _execute_revoke_staff(db, user, params, context)

    # Supplier intents
    if intent == Intent.LIST_SUPPLIERS:
        return _execute_list_suppliers(db, user, params, context)

    if intent == Intent.ADD_SUPPLIER:
        return _execute_add_supplier(db, user, params, context)

    if intent == Intent.UPDATE_SUPPLIER:
        return _execute_update_supplier(db, user, params, context)

    if intent == Intent.VIEW_SUPPLIER:
        return _execute_view_supplier(db, user, params, context)

    if intent == Intent.VIEW_SUPPLIER_PRICE_LIST:
        return _execute_view_supplier_price_list(db, user, params, context)

    if intent == Intent.VIEW_SUPPLIER_ITEMS:
        return _execute_view_supplier_items(db, user, params, context)

    # Invoice intents
    if intent == Intent.LIST_INVOICES:
        return _execute_list_invoices(db, user, params, context)

    if intent == Intent.VIEW_INVOICE:
        return _execute_view_invoice(db, user, params, context)

    # Inventory intents
    if intent == Intent.LIST_INVENTORY:
        return _execute_list_inventory(db, user, params, context)

    if intent == Intent.ADD_INVENTORY:
        return _execute_add_inventory(db, user, params, context)

    if intent == Intent.UPDATE_INVENTORY:
        return _execute_update_inventory(db, user, params, context)

    if intent == Intent.LOG_INVENTORY_USAGE:
        return _execute_log_inventory_usage(db, user, params, context)

    if intent == Intent.LIST_LOCATIONS:
        return _execute_list_locations(db, user, params, context)

    if intent == Intent.ADD_LOCATION:
        return _execute_add_location(db, user, params, context)

    # File intents
    if intent == Intent.CONFIRM_UPLOAD:
        return _execute_confirm_upload(db, user, params, context)

    # Fallback
    return ExecutionResult(response=responses.CANT_HELP, success=False)


# =============================================================================
# PROFILE HANDLERS
# =============================================================================


def _execute_view_profile(user: User) -> ExecutionResult:
    return ExecutionResult(
        response=responses.profile_view(
            name=user.full_name,
            phone=user.phone,
            username=user.username,
        )
    )


def _execute_update_name(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.UPDATE_NAME, "name"),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_NAME.value,
                "pending_params": ["name"],
                "collected_params": params,
            },
        )

    user.full_name = name
    try:
        db.commit()
        return ExecutionResult(
            response=responses.PROFILE_NAME_UPDATED.format(name=name),
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("update_name_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.PROFILE_UPDATE_ERROR, success=False)


def _execute_update_phone(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    phone = params.get("phone", "").strip()
    if not phone:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.UPDATE_PHONE, "phone"),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_PHONE.value,
                "pending_params": ["phone"],
                "collected_params": params,
            },
        )

    user.phone = phone
    user.is_phone_verified = False
    try:
        db.commit()
        return ExecutionResult(
            response=responses.PROFILE_PHONE_UPDATED.format(phone=phone),
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("update_phone_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.PROFILE_UPDATE_ERROR, success=False)


# =============================================================================
# FILE UPLOAD HANDLERS
# =============================================================================


def _execute_upload_price_list(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    supplier_id_raw = (
        params.get("supplier_id")
        or params.get("supplier")
        or params.get("supplier_name")
        or params.get("name")
    )
    if not supplier_id_raw and context.active_supplier_id:
        supplier_id_raw = context.active_supplier_id

    restaurant_id = context.active_restaurant_id
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if len(restaurants) == 1:
            restaurant_id = restaurants[0]["id"]
        elif restaurants:
            return ExecutionResult(
                response=responses.outlet_select_prompt(restaurants),
                needs_input=True,
                context_update={
                    "active_operation": Intent.UPLOAD_PRICE_LIST.value,
                    "pending_params": ["restaurant_id"],
                    "collected_params": params,
                },
            )

    supplier_id: str | None = None
    supplier_name: str | None = None

    if supplier_id_raw:
        raw_value = str(supplier_id_raw).strip()
        if _safe_uuid(raw_value):
            supplier_id = raw_value
        else:
            supplier_name = raw_value

    if supplier_id is None and supplier_name and restaurant_id:
        supplier_id = _resolve_supplier_id(db, restaurant_id, supplier_name)

    supplier = None
    if supplier_id:
        supplier = db.get(Suppliers, uuid.UUID(supplier_id))
        if supplier:
            restaurant_id = str(supplier.restaurant_id)

    if not supplier:
        prompt = responses.FILE_MISSING_SUPPLIER
        return ExecutionResult(
            response=prompt,
            needs_input=True,
            context_update={
                "active_operation": Intent.UPLOAD_PRICE_LIST.value,
                "pending_params": ["supplier_id"],
                "collected_params": {
                    **params,
                    "restaurant_id": restaurant_id,
                },
            },
        )

    return ExecutionResult(
        response=responses.FILE_UPLOAD_PRICE_LIST_PROMPT.format(supplier=supplier.name),
        needs_input=True,
        context_update={
            "active_operation": Intent.UPLOAD_PRICE_LIST.value,
            "pending_params": ["file_id"],
            "collected_params": {
                "supplier_id": str(supplier.id),
            },
            "active_restaurant_id": restaurant_id,
            "active_supplier_id": str(supplier.id),
        },
    )


# =============================================================================
# OUTLET HANDLERS
# =============================================================================


def _execute_list_outlets(db: Session, user: User) -> ExecutionResult:
    restaurants = _get_user_restaurants(db, user.id)
    return ExecutionResult(response=responses.outlets_list(restaurants))


def _execute_add_outlet(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.ADD_OUTLET, "name"),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_OUTLET.value,
                "pending_params": ["name"],
                "collected_params": params,
            },
        )

    try:
        service = RestaurantService(db)
        restaurant = service.create_restaurant(owner_user_id=user.id, name=name)
        return ExecutionResult(
            response=responses.OUTLET_CREATED.format(name=restaurant.name),
            context_update={"clear": True, "active_restaurant_id": str(restaurant.id)},
        )
    except Exception as e:
        db.rollback()
        logger.exception("add_outlet_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.OUTLET_CREATE_ERROR, success=False)


def _execute_update_outlet(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_OUTLET.value,
                "pending_params": ["restaurant_id", "name"],
                "collected_params": params,
            },
        )

    # Check ownership
    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.OUTLET_NO_ACCESS, success=False)

    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.UPDATE_OUTLET, "name"),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_OUTLET.value,
                "pending_params": ["name"],
                "collected_params": {**params, "restaurant_id": restaurant_id},
            },
        )

    restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
    if not restaurant:
        return ExecutionResult(response=responses.OUTLET_NOT_FOUND, success=False)

    restaurant.name = name
    try:
        db.commit()
        return ExecutionResult(
            response=responses.OUTLET_UPDATED.format(name=name),
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("update_outlet_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


# =============================================================================
# STAFF HANDLERS
# =============================================================================


def _execute_list_staff(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.LIST_STAFF.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    service = RestaurantService(db)
    members = service.list_members(restaurant_id=uuid.UUID(restaurant_id))

    formatted = [
        {
            "name": u.full_name,
            "username": u.username,
            "role": m.role,
        }
        for u, m in members
    ]

    return ExecutionResult(
        response=responses.staff_list(formatted),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_add_staff(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
    settings: Settings,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_STAFF.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    # Check ownership
    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.STAFF_NOT_OWNER, success=False)

    role = params.get("role", "staff").lower()
    if role not in ("staff", "owner"):
        role = "staff"

    try:
        invite = InviteCodeService(db).create_invite_code(
            restaurant_id=uuid.UUID(restaurant_id),
            target_role=role,
            created_by_user_id=user.id,
            created_by_is_superuser=False,
        )

        deep_link = InviteCodeService.deep_link(
            bot_username=settings.telegram_bot_username,
            code=invite.code,
        )

        restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
        restaurant_name = restaurant.name if restaurant else "outlet"

        return ExecutionResult(
            response=responses.staff_invite_created(deep_link, role, restaurant_name),
            context_update={"active_restaurant_id": restaurant_id},
        )
    except Exception as e:
        db.rollback()
        logger.exception("add_staff_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


def _execute_revoke_staff(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.REVOKE_STAFF.value,
                "pending_params": ["restaurant_id", "user_id"],
                "collected_params": params,
            },
        )

    # Check ownership
    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.STAFF_NOT_OWNER, success=False)

    target_user_id_raw = params.get("user_id", "").strip() if params.get("user_id") else ""
    if not target_user_id_raw:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.REVOKE_STAFF, "user_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.REVOKE_STAFF.value,
                "pending_params": ["user_id"],
                "collected_params": {**params, "restaurant_id": restaurant_id},
            },
        )

    # Resolve user_id - try UUID first, then by name/number
    target_user_id = None
    if _safe_uuid(target_user_id_raw):
        target_user_id = target_user_id_raw
    else:
        # Get members and try to match by name or number
        service = RestaurantService(db)
        members = service.list_members(restaurant_id=uuid.UUID(restaurant_id))
        
        # Try number match (1, 2, 3...)
        if target_user_id_raw.isdigit():
            idx = int(target_user_id_raw) - 1
            if 0 <= idx < len(members):
                target_user_id = str(members[idx][0].id)
        else:
            # Try name match
            search_lower = target_user_id_raw.lower()
            for u, m in members:
                if u.full_name and search_lower in u.full_name.lower():
                    target_user_id = str(u.id)
                    break
                if u.username and search_lower in u.username.lower():
                    target_user_id = str(u.id)
                    break
    
    if not target_user_id:
        return ExecutionResult(response="User not found. Please provide a valid user name or number.", success=False)

    # Can't revoke self
    if target_user_id == str(user.id):
        return ExecutionResult(response=responses.STAFF_CANT_REVOKE_SELF, success=False)

    membership = db.scalar(
        select(RestaurantUser).where(
            RestaurantUser.restaurant_id == uuid.UUID(restaurant_id),
            RestaurantUser.user_id == uuid.UUID(target_user_id),
            RestaurantUser.status != "removed",
        )
    )

    if not membership:
        return ExecutionResult(response="User is not a member of this outlet.", success=False)

    try:
        membership.status = "removed"
        db.commit()

        target_user = db.get(User, uuid.UUID(target_user_id))
        name = target_user.full_name if target_user else "User"
        restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
        restaurant_name = restaurant.name if restaurant else "outlet"

        return ExecutionResult(
            response=responses.STAFF_REVOKED.format(name=name, restaurant=restaurant_name),
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("revoke_staff_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.STAFF_REVOKE_ERROR, success=False)


# =============================================================================
# SUPPLIER HANDLERS
# =============================================================================


def _execute_list_suppliers(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.LIST_SUPPLIERS.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    suppliers = db.scalars(
        select(Suppliers).where(
            Suppliers.restaurant_id == uuid.UUID(restaurant_id),
            Suppliers.is_active == True,
        )
    ).all()

    formatted = [{"id": str(s.id), "name": s.name} for s in suppliers]

    return ExecutionResult(
        response=responses.suppliers_list(formatted),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_add_supplier(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_SUPPLIER.value,
                "pending_params": ["restaurant_id", "name"],
                "collected_params": params,
            },
        )

    # Check ownership (only owners can add suppliers)
    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(
            response="Only outlet owners can add suppliers.",
            success=False,
        )

    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.ADD_SUPPLIER, "name"),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_SUPPLIER.value,
                "pending_params": ["name"],
                "collected_params": {**params, "restaurant_id": restaurant_id},
            },
        )

    try:
        supplier = Suppliers(
            restaurant_id=uuid.UUID(restaurant_id),
            name=name,
            is_active=True,
        )
        db.add(supplier)
        db.commit()

        return ExecutionResult(
            response=responses.SUPPLIER_CREATED.format(name=name),
            context_update={
                "clear": True,
                "active_restaurant_id": restaurant_id,
                "active_supplier_id": str(supplier.id),
            },
        )
    except Exception as e:
        db.rollback()
        logger.exception("add_supplier_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.SUPPLIER_CREATE_ERROR, success=False)


def _execute_update_supplier(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    supplier_id_raw = params.get("supplier_id", "").strip() if params.get("supplier_id") else ""
    if not supplier_id_raw and context.active_supplier_id:
        supplier_id_raw = context.active_supplier_id
    if not supplier_id_raw:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.UPDATE_SUPPLIER, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_SUPPLIER.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    # Try to resolve supplier_id - may need restaurant context
    restaurant_id = context.active_restaurant_id
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if len(restaurants) == 1:
            restaurant_id = restaurants[0]["id"]
    
    supplier_id = None
    if restaurant_id:
        supplier_id = _resolve_supplier_id(db, restaurant_id, supplier_id_raw)
    
    if not supplier_id:
        # Try direct UUID parse as fallback
        supplier_id = supplier_id_raw if _safe_uuid(supplier_id_raw) else None
    
    if not supplier_id:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    # Check ownership
    if not _is_restaurant_owner(db, user.id, supplier.restaurant_id):
        return ExecutionResult(
            response="Only outlet owners can update suppliers.",
            success=False,
        )

    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response="What's the new name for this supplier?",
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_SUPPLIER.value,
                "pending_params": ["name"],
                "collected_params": {**params, "supplier_id": supplier_id},
            },
        )

    try:
        supplier.name = name
        db.commit()
        return ExecutionResult(
            response=responses.SUPPLIER_UPDATED.format(name=name),
            context_update={"clear": True, "active_supplier_id": str(supplier.id)},
        )
    except Exception as e:
        db.rollback()
        logger.exception("update_supplier_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


def _execute_view_supplier(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    supplier_id_raw = params.get("supplier_id", "").strip() if params.get("supplier_id") else ""
    if not supplier_id_raw and context.active_supplier_id:
        supplier_id_raw = context.active_supplier_id
    if not supplier_id_raw:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.VIEW_SUPPLIER, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.VIEW_SUPPLIER.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    # Try to resolve supplier_id
    restaurant_id = context.active_restaurant_id
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if len(restaurants) == 1:
            restaurant_id = restaurants[0]["id"]
    
    supplier_id = None
    if restaurant_id:
        supplier_id = _resolve_supplier_id(db, restaurant_id, supplier_id_raw)
    
    if not supplier_id:
        supplier_id = supplier_id_raw if _safe_uuid(supplier_id_raw) else None
    
    if not supplier_id:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    return ExecutionResult(
        response=responses.supplier_details({
            "name": supplier.name,
            "currency": supplier.currency,
            "lead_time_days": supplier.lead_time_days,
            "notes": supplier.notes,
        }),
        context_update={"active_supplier_id": str(supplier.id)},
    )


def _execute_view_supplier_price_list(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """View supplier's current price list."""
    supplier_id_raw = params.get("supplier_id", "").strip() if params.get("supplier_id") else ""
    if not supplier_id_raw and context.active_supplier_id:
        supplier_id_raw = context.active_supplier_id
    if not supplier_id_raw:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.VIEW_SUPPLIER_PRICE_LIST, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.VIEW_SUPPLIER_PRICE_LIST.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    # Resolve supplier_id
    restaurant_id = context.active_restaurant_id
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if len(restaurants) == 1:
            restaurant_id = restaurants[0]["id"]
    
    supplier_id = None
    if restaurant_id:
        supplier_id = _resolve_supplier_id(db, restaurant_id, supplier_id_raw)
    
    if not supplier_id:
        supplier_id = supplier_id_raw if _safe_uuid(supplier_id_raw) else None
    
    if not supplier_id:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    from app.db.models.supplier_items import SupplierItems
    from app.db.models.supplier_prices import SupplierPrices

    # Get latest prices for this supplier's items
    prices = db.execute(
        select(SupplierItems, SupplierPrices)
        .outerjoin(
            SupplierPrices,
            (SupplierPrices.supplier_item_id == SupplierItems.id) &
            (SupplierPrices.is_current == True)
        )
        .where(SupplierItems.supplier_id == uuid.UUID(supplier_id))
        .order_by(SupplierItems.supplier_sku.asc())
    ).all()

    formatted = [
        {
            "name": item.supplier_name,
            "sku": item.supplier_sku,
            "unit": item.supplier_unit,
            "price": float(price.unit_price) if price else None,
            "currency": price.currency if price else supplier.currency,
        }
        for item, price in prices
    ]

    return ExecutionResult(
        response=responses.supplier_price_list(supplier.name, formatted),
        context_update={"active_supplier_id": str(supplier.id)},
    )


def _execute_view_supplier_items(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """View items offered by a supplier."""
    supplier_id_raw = params.get("supplier_id", "").strip() if params.get("supplier_id") else ""
    if not supplier_id_raw and context.active_supplier_id:
        supplier_id_raw = context.active_supplier_id
    if not supplier_id_raw:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.VIEW_SUPPLIER_ITEMS, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.VIEW_SUPPLIER_ITEMS.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    # Resolve supplier_id
    restaurant_id = context.active_restaurant_id
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if len(restaurants) == 1:
            restaurant_id = restaurants[0]["id"]
    
    supplier_id = None
    if restaurant_id:
        supplier_id = _resolve_supplier_id(db, restaurant_id, supplier_id_raw)
    
    if not supplier_id:
        supplier_id = supplier_id_raw if _safe_uuid(supplier_id_raw) else None
    
    if not supplier_id:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    from app.db.models.supplier_items import SupplierItems

    items = db.scalars(
        select(SupplierItems)
        .where(SupplierItems.supplier_id == uuid.UUID(supplier_id))
        .order_by(SupplierItems.supplier_name.asc())
    ).all()

    formatted = [
        {
            "name": item.supplier_name,
            "sku": item.supplier_sku,
            "unit": item.supplier_unit,
        }
        for item in items
    ]

    return ExecutionResult(
        response=responses.supplier_items_list(supplier.name, formatted),
        context_update={"active_supplier_id": str(supplier.id)},
    )


# =============================================================================
# INVOICE HANDLERS
# =============================================================================


def _execute_list_invoices(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """List invoices for a restaurant."""
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.LIST_INVOICES.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    from app.db.models.invoices import Invoices

    invoices = db.scalars(
        select(Invoices)
        .where(Invoices.restaurant_id == uuid.UUID(restaurant_id))
        .order_by(Invoices.invoice_date.desc())
        .limit(20)
    ).all()

    formatted = [
        {
            "id": str(inv.id),
            "date": inv.invoice_date.strftime("%Y-%m-%d") if inv.invoice_date else "N/A",
            "supplier": inv.supplier_name,
            "total": float(inv.total_amount) if inv.total_amount else None,
            "currency": inv.currency,
            "status": inv.status,
        }
        for inv in invoices
    ]

    return ExecutionResult(
        response=responses.invoices_list(formatted),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_view_invoice(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """View invoice details with line items."""
    invoice_id = params.get("invoice_id", "").strip()
    if not invoice_id:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.VIEW_INVOICE, "invoice_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.VIEW_INVOICE.value,
                "pending_params": ["invoice_id"],
                "collected_params": params,
            },
        )

    from app.db.models.invoices import Invoices
    from app.db.models.invoice_line_items import InvoiceLineItems

    try:
        invoice = db.get(Invoices, uuid.UUID(invoice_id))
    except ValueError:
        return ExecutionResult(response="Invoice not found.", success=False)

    if not invoice:
        return ExecutionResult(response="Invoice not found.", success=False)

    line_items = db.scalars(
        select(InvoiceLineItems)
        .where(InvoiceLineItems.invoice_id == uuid.UUID(invoice_id))
        .order_by(InvoiceLineItems.id.asc())
    ).all()

    formatted_items = [
        {
            "description": li.description,
            "quantity": float(li.quantity) if li.quantity else None,
            "unit": li.unit,
            "unit_price": float(li.unit_price) if li.unit_price else None,
            "total": float(li.total_amount) if li.total_amount else None,
        }
        for li in line_items
    ]

    return ExecutionResult(
        response=responses.invoice_details(
            invoice={
                "date": invoice.invoice_date.strftime("%Y-%m-%d") if invoice.invoice_date else "N/A",
                "supplier": invoice.supplier_name,
                "invoice_number": invoice.invoice_number,
                "total": float(invoice.total_amount) if invoice.total_amount else None,
                "currency": invoice.currency,
                "status": invoice.status,
            },
            line_items=formatted_items,
        ),
    )


# =============================================================================
# INVENTORY HANDLERS
# =============================================================================


def _execute_list_inventory(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.LIST_INVENTORY.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    from app.db.models.inventory_batches import InventoryBatches
    from app.db.models.products import Products

    batches = db.execute(
        select(InventoryBatches, Products.name_en)
        .outerjoin(Products, InventoryBatches.product_id == Products.id)
        .where(InventoryBatches.restaurant_id == uuid.UUID(restaurant_id))
        .order_by(InventoryBatches.received_date.desc())
        .limit(50)
    ).all()

    formatted = [
        {
            "id": str(b.id),
            "product_name": p_name or "Unknown",
            "quantity": float(b.quantity),
            "unit": b.unit,
            "status": b.status,
        }
        for b, p_name in batches
    ]

    return ExecutionResult(
        response=responses.inventory_list(formatted),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_add_inventory(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    # This is a complex operation - for now, direct to file upload
    return ExecutionResult(
        response="To add inventory, please upload an invoice or manually record it. "
        "Upload a file and I'll help you process it.",
        context_update={"clear": True},
    )


def _execute_update_inventory(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    # This is a complex operation
    return ExecutionResult(
        response="To update inventory, please specify the batch and the change you'd like to make.",
        context_update={"clear": True},
    )


def _execute_log_inventory_usage(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """Log inventory usage, waste, or consumption."""
    batch_id = params.get("batch_id", "").strip()
    if not batch_id:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.LOG_INVENTORY_USAGE, "batch_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.LOG_INVENTORY_USAGE.value,
                "pending_params": ["batch_id", "quantity", "reason"],
                "collected_params": params,
            },
        )

    quantity = params.get("quantity")
    if not quantity:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.LOG_INVENTORY_USAGE, "quantity"),
            needs_input=True,
            context_update={
                "active_operation": Intent.LOG_INVENTORY_USAGE.value,
                "pending_params": ["quantity", "reason"],
                "collected_params": {**params, "batch_id": batch_id},
            },
        )

    reason = params.get("reason", "").strip()
    if not reason:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.LOG_INVENTORY_USAGE, "reason"),
            needs_input=True,
            context_update={
                "active_operation": Intent.LOG_INVENTORY_USAGE.value,
                "pending_params": ["reason"],
                "collected_params": {**params, "batch_id": batch_id, "quantity": quantity},
            },
        )

    from app.db.models.inventory_batches import InventoryBatches
    from app.db.models.inventory_movements import InventoryMovements
    from decimal import Decimal

    try:
        batch = db.get(InventoryBatches, uuid.UUID(batch_id))
    except ValueError:
        return ExecutionResult(response="Inventory batch not found.", success=False)

    if not batch:
        return ExecutionResult(response="Inventory batch not found.", success=False)

    try:
        qty = Decimal(str(quantity))
        if qty <= 0:
            return ExecutionResult(response="Quantity must be positive.", success=False)

        # Check sufficient quantity
        if batch.quantity < qty:
            return ExecutionResult(
                response=f"Insufficient quantity. Current: {batch.quantity} {batch.unit}",
                success=False,
            )

        # Create movement record
        movement = InventoryMovements(
            batch_id=uuid.UUID(batch_id),
            movement_type=reason.lower() if reason.lower() in ("usage", "waste", "expired", "transfer") else "usage",
            quantity=qty,
            reason=reason,
            recorded_by_user_id=user.id,
        )
        db.add(movement)

        # Update batch quantity
        batch.quantity = batch.quantity - qty
        if batch.quantity <= 0:
            batch.status = "depleted"

        db.commit()

        return ExecutionResult(
            response=f"Logged {qty} {batch.unit} as {reason}. Remaining: {batch.quantity} {batch.unit}",
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("log_inventory_usage_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


def _execute_list_locations(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """List inventory storage locations."""
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.LIST_LOCATIONS.value,
                "pending_params": ["restaurant_id"],
                "collected_params": params,
            },
        )

    from app.db.models.inventory_locations import InventoryLocations

    locations = db.scalars(
        select(InventoryLocations)
        .where(InventoryLocations.restaurant_id == uuid.UUID(restaurant_id))
        .order_by(InventoryLocations.name.asc())
    ).all()

    formatted = [
        {
            "id": str(loc.id),
            "name": loc.name,
            "type": loc.location_type,
        }
        for loc in locations
    ]

    return ExecutionResult(
        response=responses.locations_list(formatted),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_add_location(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """Add a new inventory storage location."""
    restaurant_id, error = _resolve_restaurant_id(db, user.id, context, params)
    if error:
        return ExecutionResult(response=error, success=False)

    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_LOCATION.value,
                "pending_params": ["restaurant_id", "name"],
                "collected_params": params,
            },
        )

    # Check ownership (only owners can add locations)
    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(
            response="Only outlet owners can add storage locations.",
            success=False,
        )

    name = params.get("name", "").strip()
    if not name:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.ADD_LOCATION, "name"),
            needs_input=True,
            context_update={
                "active_operation": Intent.ADD_LOCATION.value,
                "pending_params": ["name"],
                "collected_params": {**params, "restaurant_id": restaurant_id},
            },
        )

    from app.db.models.inventory_locations import InventoryLocations

    try:
        location = InventoryLocations(
            restaurant_id=uuid.UUID(restaurant_id),
            name=name,
            location_type="general",  # Default type
        )
        db.add(location)
        db.commit()

        return ExecutionResult(
            response=f"Storage location '{name}' created successfully.",
            context_update={"clear": True, "active_restaurant_id": restaurant_id},
        )
    except Exception as e:
        db.rollback()
        logger.exception("add_location_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


# =============================================================================
# FILE PROCESSING HANDLERS
# =============================================================================


def _execute_confirm_upload(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    staging_id = params.get("staging_id") or context.staging_id
    if not staging_id:
        return ExecutionResult(
            response="Nothing to confirm. Upload a file first.",
            success=False,
        )

    try:
        staging = db.get(FileProcessingStaging, uuid.UUID(staging_id))
    except ValueError:
        return ExecutionResult(response="Invalid staging ID.", success=False)

    if not staging:
        return ExecutionResult(response="Upload not found.", success=False)

    if staging.status != "pending_review":
        return ExecutionResult(
            response=f"This upload is already {staging.status}.",
            success=False,
        )

    # Import confirm logic from file_processing tools
    from app.ai.db_tools.file_processing import create_file_processing_tools

    # Create temporary tools to access confirm handler
    tools = create_file_processing_tools(
        db=db,
        user_id=user.id,
        actor_role="owner",  # Simplified
        restaurant_roles={},
    )

    confirm_tool = tools.get("confirm_file_processing")
    if not confirm_tool:
        return ExecutionResult(response=responses.FILE_CONFIRM_ERROR, success=False)

    result = confirm_tool.handler({"staging_id": staging_id})

    if result.startswith("Error"):
        return ExecutionResult(response=result, success=False)

    return ExecutionResult(
        response=responses.FILE_CONFIRMED.format(summary=result),
        context_update={"clear": True},
    )
