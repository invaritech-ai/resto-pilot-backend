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
    staging_id: str | None = None  # For file processing

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_operation": self.active_operation,
            "pending_params": self.pending_params,
            "collected_params": self.collected_params,
            "active_restaurant_id": self.active_restaurant_id,
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


def _resolve_restaurant_id(
    db: Session,
    user_id: uuid.UUID,
    context: UserContext,
    params: dict[str, Any],
) -> tuple[str | None, str | None]:
    """
    Resolve restaurant_id from params, context, or auto-select if only one.
    Returns (restaurant_id, error_message).
    """
    # Check if provided in params
    if params.get("restaurant_id"):
        return params["restaurant_id"], None

    # Check context
    if context.active_restaurant_id:
        return context.active_restaurant_id, None

    # Auto-select if user has only one restaurant
    restaurants = _get_user_restaurants(db, user_id)
    if not restaurants:
        return None, responses.ERROR_NO_OUTLETS
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

    # Inventory intents
    if intent == Intent.LIST_INVENTORY:
        return _execute_list_inventory(db, user, params, context)

    if intent == Intent.ADD_INVENTORY:
        return _execute_add_inventory(db, user, params, context)

    if intent == Intent.UPDATE_INVENTORY:
        return _execute_update_inventory(db, user, params, context)

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

    target_user_id = params.get("user_id", "").strip()
    if not target_user_id:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.REVOKE_STAFF, "user_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.REVOKE_STAFF.value,
                "pending_params": ["user_id"],
                "collected_params": {**params, "restaurant_id": restaurant_id},
            },
        )

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
            context_update={"clear": True, "active_restaurant_id": restaurant_id},
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
    supplier_id = params.get("supplier_id", "").strip()
    if not supplier_id:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.UPDATE_SUPPLIER, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.UPDATE_SUPPLIER.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    try:
        supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    except ValueError:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

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
            context_update={"clear": True},
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
    supplier_id = params.get("supplier_id", "").strip()
    if not supplier_id:
        return ExecutionResult(
            response=get_missing_param_prompt(Intent.VIEW_SUPPLIER, "supplier_id"),
            needs_input=True,
            context_update={
                "active_operation": Intent.VIEW_SUPPLIER.value,
                "pending_params": ["supplier_id"],
                "collected_params": params,
            },
        )

    try:
        supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    except ValueError:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    return ExecutionResult(
        response=responses.supplier_details({
            "name": supplier.name,
            "currency": supplier.currency,
            "lead_time_days": supplier.lead_time_days,
            "notes": supplier.notes,
        })
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
