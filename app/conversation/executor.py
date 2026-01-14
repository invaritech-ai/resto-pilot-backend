"""
Intent executor that maps resolved intents to database operations.

Simplified version that expects all params to be resolved by the intent resolver.
No validation loops or multi-step prompting.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent
from app.ai.intent_resolver import ResolvedIntent
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
    # Context to persist
    context_update: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserContext:
    """User's current context - simplified to just active entities."""

    active_restaurant_id: str | None = None
    active_supplier_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_restaurant_id": self.active_restaurant_id,
            "active_supplier_id": self.active_supplier_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UserContext":
        if not data:
            return cls()
        return cls(
            active_restaurant_id=data.get("active_restaurant_id"),
            active_supplier_id=data.get("active_supplier_id"),
        )


def _get_user_restaurants(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get list of restaurants user has access to."""
    service = RestaurantService(db)
    rows = service.list_for_user(user_id=user_id)
    return [{"id": str(r.id), "name": r.name, "role": m.role} for r, m in rows]


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


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    """Safely parse a UUID string, returning None if invalid."""
    if not value:
        return None
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError):
        return None


def _extract_invite_code(value: str) -> str:
    """Extract invite code from a raw code or Telegram deep link."""
    raw = value.strip()
    if not raw:
        return ""
    if "://" in raw:
        try:
            parsed = urlparse(raw)
            query = parse_qs(parsed.query)
            start_vals = query.get("start")
            if start_vals:
                return start_vals[0].strip()
        except Exception:
            return raw
    return raw


def _get_restaurant_id(
    params: dict[str, Any],
    context: UserContext,
    db: Session,
    user_id: uuid.UUID,
) -> str | None:
    """Get restaurant_id from params, context, or auto-select if only one.
    
    SECURITY: Validates that the user has access to the restaurant.
    """
    # Check params first
    restaurant_id = params.get("restaurant_id")
    if restaurant_id:
        restaurant_id_str = (
            str(restaurant_id).strip()
            if isinstance(restaurant_id, str)
            else str(restaurant_id)
        )
        # Validate access
        restaurants = _get_user_restaurants(db, user_id)
        if any(r["id"] == restaurant_id_str for r in restaurants):
            return restaurant_id_str
        # Invalid or unauthorized - return None to trigger error

    # Check context
    if context.active_restaurant_id:
        # Validate context restaurant is still accessible
        restaurants = _get_user_restaurants(db, user_id)
        if any(r["id"] == context.active_restaurant_id for r in restaurants):
            return context.active_restaurant_id

    # Auto-select if only one restaurant
    restaurants = _get_user_restaurants(db, user_id)
    if len(restaurants) == 1:
        return restaurants[0]["id"]

    return None


def _get_supplier_id(
    params: dict[str, Any],
    context: UserContext,
    db: Session,
    user_id: uuid.UUID,
) -> str | None:
    """Get supplier_id from params or context.
    
    SECURITY: Validates that the supplier belongs to a restaurant the user has access to.
    """
    supplier_id = params.get("supplier_id")
    if supplier_id:
        supplier_id_str = (
            str(supplier_id).strip()
            if isinstance(supplier_id, str)
            else str(supplier_id)
        )
        # Validate access - check supplier belongs to user's restaurant
        try:
            supplier = db.get(Suppliers, uuid.UUID(supplier_id_str))
            if supplier:
                # Check user has access to the supplier's restaurant
                restaurants = _get_user_restaurants(db, user_id)
                if any(r["id"] == str(supplier.restaurant_id) for r in restaurants):
                    return supplier_id_str
        except (ValueError, AttributeError):
            pass
        # Invalid or unauthorized - return None
    
    # Check context
    if context.active_supplier_id:
        # Validate context supplier is still accessible
        try:
            supplier = db.get(Suppliers, uuid.UUID(context.active_supplier_id))
            if supplier:
                restaurants = _get_user_restaurants(db, user_id)
                if any(r["id"] == str(supplier.restaurant_id) for r in restaurants):
                    return context.active_supplier_id
        except (ValueError, AttributeError):
            pass
    
    return None


def execute_intent(
    *,
    resolved: ResolvedIntent,
    db: Session,
    user: User,
    context: UserContext,
    settings: Settings | None = None,
) -> ExecutionResult:
    """
    Execute a resolved intent.

    Args:
        resolved: The resolved intent with fully resolved params
        db: Database session
        user: Current user
        context: User's context (active restaurant/supplier)
        settings: App settings (optional, will load if not provided)

    Returns:
        ExecutionResult with response text and context updates
    """
    settings = settings or get_settings()
    intent = resolved.intent
    params = resolved.params

    # Handle clarification needed
    if resolved.needs_clarification and resolved.clarification_message:
        return ExecutionResult(response=resolved.clarification_message, success=False)

    # Navigation intents
    if intent == Intent.SHOW_MENU:
        return ExecutionResult(response=responses.MAIN_MENU)

    if intent == Intent.CANCEL:
            return ExecutionResult(
            response=responses.CANCEL_SUCCESS, context_update={"clear": True}
            )

    if intent == Intent.UNKNOWN:
        return ExecutionResult(response=responses.CANT_HELP, success=False)

    # Profile intents
    if intent == Intent.VIEW_PROFILE:
        return _execute_view_profile(user)

    if intent == Intent.HELP:
        topic = params.get("topic")
        return ExecutionResult(response=responses.help_topic(topic))

    if intent == Intent.UPDATE_NAME:
        return _execute_update_name(db, user, params)

    if intent == Intent.UPDATE_PHONE:
        return _execute_update_phone(db, user, params)

    if intent == Intent.UPLOAD_PRICE_LIST:
        return _execute_upload_price_list(db, user, params, context)

    # Outlet intents
    if intent == Intent.LIST_OUTLETS:
        return _execute_list_outlets(db, user)

    if intent == Intent.ADD_OUTLET:
        return _execute_add_outlet(db, user, params)

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

    if intent == Intent.DEACTIVATE_SUPPLIER:
        return _execute_deactivate_supplier(db, user, params, context)

    # Invite intents
    if intent == Intent.LIST_INVITES:
        return _execute_list_invites(db, user, params, context, settings)

    if intent == Intent.MOVE_INVITE:
        return _execute_move_invite(db, user, params, context)

    # Invoice intents
    if intent == Intent.LIST_INVOICES:
        return _execute_list_invoices(db, user, params, context)

    if intent == Intent.VIEW_INVOICE:
        return _execute_view_invoice(db, user, params)

    # Inventory intents
    if intent == Intent.LIST_INVENTORY:
        return _execute_list_inventory(db, user, params, context)

    if intent == Intent.ADD_INVENTORY:
        return _execute_add_inventory()

    if intent == Intent.UPDATE_INVENTORY:
        return _execute_update_inventory()

    if intent == Intent.LOG_INVENTORY_USAGE:
        return _execute_log_inventory_usage(db, user, params)

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
) -> ExecutionResult:
    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide the name you want to use.", success=False
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
) -> ExecutionResult:
    phone_raw = params.get("phone")
    phone = str(phone_raw).strip() if phone_raw else ""
    if not phone:
        return ExecutionResult(
            response="Please provide your phone number.", success=False
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
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(response=responses.FILE_MISSING_SUPPLIER, success=False)

        supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    return ExecutionResult(
        response=responses.FILE_UPLOAD_PRICE_LIST_PROMPT.format(supplier=supplier.name),
        context_update={
            "active_restaurant_id": str(supplier.restaurant_id),
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
) -> ExecutionResult:
    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide a name for the outlet.", success=False
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.OUTLET_NO_ACCESS, success=False)

    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide the new name for the outlet.", success=False
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    service = RestaurantService(db)
    members = service.list_members(restaurant_id=uuid.UUID(restaurant_id))
    restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))

    formatted = [
        {"name": u.full_name, "username": u.username, "role": m.role}
        for u, m in members
    ]

    return ExecutionResult(
        response=responses.staff_list(
            formatted,
            outlet_name=restaurant.name if restaurant else None,
        ),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_add_staff(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
    settings: Settings,
) -> ExecutionResult:
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.STAFF_NOT_OWNER, success=False)

    role_raw = params.get("role", "staff")
    role = str(role_raw).lower() if role_raw else "staff"
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        return ExecutionResult(response="Please specify which outlet.", success=False)

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.STAFF_NOT_OWNER, success=False)

    target_user_id_raw = params.get("user_id")
    target_user_id = str(target_user_id_raw).strip() if target_user_id_raw else ""
    if not target_user_id:
        return ExecutionResult(
            response="Please specify which staff member to remove.", success=False
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
        return ExecutionResult(
            response="User is not a member of this outlet.", success=False
        )

    try:
        membership.status = "removed"
        db.commit()

        target_user = db.get(User, uuid.UUID(target_user_id))
        name = target_user.full_name if target_user else "User"
        restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
        restaurant_name = restaurant.name if restaurant else "outlet"

        return ExecutionResult(
            response=responses.STAFF_REVOKED.format(
                name=name, restaurant=restaurant_name
            ),
            context_update={"clear": True},
        )
    except Exception as e:
        db.rollback()
        logger.exception("revoke_staff_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.STAFF_REVOKE_ERROR, success=False)


# =============================================================================
# INVITE HANDLERS
# =============================================================================


def _execute_list_invites(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
    settings: Settings,
) -> ExecutionResult:
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.STAFF_NOT_OWNER, success=False)

    from app.db.models.invite_codes import InviteCodes

    now = dt.datetime.now(dt.UTC)
    invites = db.scalars(
        select(InviteCodes).where(InviteCodes.restaurant_id == uuid.UUID(restaurant_id))
    ).all()

    active_invites = []
    for invite in invites:
        if invite.used_at is not None:
            continue
        if invite.expires_at is not None and invite.expires_at <= now:
            continue
        deep_link = InviteCodeService.deep_link(
            bot_username=settings.telegram_bot_username,
            code=invite.code,
        )
        active_invites.append(
            {
                "code": invite.code,
                "role": invite.role,
                "expires_at": invite.expires_at.strftime("%Y-%m-%d")
                if invite.expires_at
                else "Never",
                "link": deep_link,
            }
        )

    restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
    restaurant_name = restaurant.name if restaurant else "outlet"

    return ExecutionResult(
        response=responses.invites_list(active_invites, restaurant_name),
        context_update={"active_restaurant_id": restaurant_id},
    )


def _execute_move_invite(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        return ExecutionResult(
            response="Please specify which outlet to move the invite to.", success=False
        )

    invite_code_raw = (
        params.get("invite_code")
        or params.get("code")
        or params.get("invite")
        or params.get("invite_link")
    )
    if invite_code_raw:
        invite_code = _extract_invite_code(str(invite_code_raw))
    else:
        invite_code = ""
    if not invite_code:
        return ExecutionResult(response=responses.INVITE_MOVE_NEED_CODE, success=False)

    from app.db.models.invite_codes import InviteCodes

    invite = db.scalar(select(InviteCodes).where(InviteCodes.code == invite_code))
    if not invite:
        return ExecutionResult(response=responses.INVITE_MOVE_NOT_ACTIVE, success=False)

    now = dt.datetime.now(dt.UTC)
    if invite.used_at is not None or (
        invite.expires_at is not None and invite.expires_at <= now
    ):
        return ExecutionResult(response=responses.INVITE_MOVE_NOT_ACTIVE, success=False)

    if not _is_restaurant_owner(
        db, user.id, invite.restaurant_id
    ) or not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(response=responses.INVITE_MOVE_NOT_OWNER, success=False)

    if str(invite.restaurant_id) == restaurant_id:
        restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
        restaurant_name = restaurant.name if restaurant else "this outlet"
        return ExecutionResult(
            response=responses.INVITE_MOVE_ALREADY_TARGET.format(
                restaurant=restaurant_name
            ),
            context_update={"active_restaurant_id": restaurant_id},
        )

    try:
        invite.restaurant_id = uuid.UUID(restaurant_id)
        db.commit()
        restaurant = db.get(Restaurant, uuid.UUID(restaurant_id))
        restaurant_name = restaurant.name if restaurant else "the outlet"
        return ExecutionResult(
            response=responses.INVITE_MOVE_SUCCESS.format(
                code=invite.code, restaurant=restaurant_name
            ),
            context_update={"active_restaurant_id": restaurant_id},
        )
    except Exception as e:
        db.rollback()
        logger.exception("move_invite_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


# =============================================================================
# SUPPLIER HANDLERS
# =============================================================================


def _execute_list_suppliers(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(
            response="Only outlet owners can add suppliers.", success=False
        )

    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide the supplier name.", success=False
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


def _execute_deactivate_supplier(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(
            response="Please specify which supplier to deactivate.", success=False
        )

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    if not _is_restaurant_owner(db, user.id, supplier.restaurant_id):
        return ExecutionResult(
            response="Only outlet owners can deactivate suppliers.", success=False
        )

    if not supplier.is_active:
        return ExecutionResult(
            response=responses.SUPPLIER_ALREADY_INACTIVE.format(name=supplier.name),
            context_update={"active_supplier_id": str(supplier.id)},
        )

    try:
        supplier.is_active = False
        db.commit()
        return ExecutionResult(
            response=responses.SUPPLIER_DEACTIVATED.format(name=supplier.name),
            context_update={
                "clear": True,
                "active_restaurant_id": str(supplier.restaurant_id),
                "active_supplier_id": str(supplier.id),
            },
        )
    except Exception as e:
        db.rollback()
        logger.exception("deactivate_supplier_failed", extra={"error": str(e)})
        return ExecutionResult(response=responses.ERROR_GENERIC, success=False)


def _execute_update_supplier(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(
            response="Please specify which supplier to update.", success=False
        )

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    if not _is_restaurant_owner(db, user.id, supplier.restaurant_id):
        return ExecutionResult(
            response="Only outlet owners can update suppliers.", success=False
        )

    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide the new name for this supplier.", success=False
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
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(
            response="Please specify which supplier to view.", success=False
        )

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    return ExecutionResult(
        response=responses.supplier_details(
            {
            "name": supplier.name,
            "currency": supplier.currency,
            "lead_time_days": supplier.lead_time_days,
            "notes": supplier.notes,
            }
        ),
        context_update={"active_supplier_id": str(supplier.id)},
    )


def _execute_view_supplier_price_list(
    db: Session,
    user: User,
    params: dict[str, Any],
    context: UserContext,
) -> ExecutionResult:
    """View supplier's current price list."""
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(
            response="Please specify which supplier's price list to view.",
            success=False,
        )

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    from app.db.models.supplier_items import SupplierItems
    from app.db.models.supplier_prices import SupplierPrices

    now = dt.datetime.now(dt.UTC)
    prices = db.execute(
        select(SupplierItems, SupplierPrices)
        .outerjoin(
            SupplierPrices,
            (SupplierPrices.supplier_item_id == SupplierItems.id)
            & (SupplierPrices.valid_from <= now)
            & ((SupplierPrices.valid_to.is_(None)) | (SupplierPrices.valid_to >= now)),
        )
        .where(SupplierItems.supplier_id == uuid.UUID(supplier_id))
        .order_by(SupplierItems.supplier_sku.asc())
    ).all()

    formatted = [
        {
            "name": item.supplier_name_raw,
            "sku": item.supplier_sku,
            "unit": item.unit_basis,
            "price": float(price.price) if price else None,
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
    supplier_id = _get_supplier_id(params, context, db, user.id)
    if not supplier_id:
        return ExecutionResult(
            response="Please specify which supplier's items to view.", success=False
        )

    supplier = db.get(Suppliers, uuid.UUID(supplier_id))
    if not supplier:
        return ExecutionResult(response=responses.SUPPLIER_NOT_FOUND, success=False)

    from app.db.models.supplier_items import SupplierItems

    items = db.scalars(
        select(SupplierItems)
        .where(SupplierItems.supplier_id == uuid.UUID(supplier_id))
        .order_by(SupplierItems.supplier_name_raw.asc())
    ).all()

    formatted = [
        {
            "name": item.supplier_name_raw,
            "sku": item.supplier_sku,
            "unit": item.unit_basis,
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
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
            "date": inv.invoice_date.strftime("%Y-%m-%d")
            if inv.invoice_date
            else "N/A",
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
) -> ExecutionResult:
    """View invoice details with line items."""
    invoice_id_raw = params.get("invoice_id")
    invoice_id = str(invoice_id_raw).strip() if invoice_id_raw else ""
    if not invoice_id:
        return ExecutionResult(
            response="Please specify which invoice to view.", success=False
        )

    from app.db.models.invoices import Invoices
    from app.db.models.invoice_line_items import InvoiceLineItems

    try:
        invoice = db.get(Invoices, uuid.UUID(invoice_id))
    except ValueError:
        return ExecutionResult(response="Invoice not found.", success=False)

    if not invoice:
        return ExecutionResult(response="Invoice not found.", success=False)
    
    # SECURITY: Validate user has access to the invoice's restaurant
    restaurants = _get_user_restaurants(db, user.id)
    if not any(r["id"] == str(invoice.restaurant_id) for r in restaurants):
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
                "date": invoice.invoice_date.strftime("%Y-%m-%d")
                if invoice.invoice_date
                else "N/A",
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
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


def _execute_add_inventory() -> ExecutionResult:
    return ExecutionResult(
        response="To add inventory, please upload an invoice or manually record it. "
        "Upload a file and I'll help you process it.",
        context_update={"clear": True},
    )


def _execute_update_inventory() -> ExecutionResult:
    return ExecutionResult(
        response="To update inventory, please specify the batch and the change you'd like to make.",
        context_update={"clear": True},
    )


def _execute_log_inventory_usage(
    db: Session,
    user: User,
    params: dict[str, Any],
) -> ExecutionResult:
    """Log inventory usage, waste, or consumption."""
    batch_id_raw = params.get("batch_id")
    batch_id = str(batch_id_raw).strip() if batch_id_raw else ""
    if not batch_id:
        return ExecutionResult(
            response="Please specify which inventory batch.", success=False
        )

    quantity = params.get("quantity")
    if not quantity:
        return ExecutionResult(response="Please specify the quantity.", success=False)

    reason_raw = params.get("reason", "usage")
    reason = str(reason_raw).strip() if reason_raw else "usage"

    from app.db.models.inventory_batches import InventoryBatches
    from app.db.models.inventory_movements import InventoryMovements
    from decimal import Decimal

    try:
        batch = db.get(InventoryBatches, uuid.UUID(batch_id))
    except ValueError:
        return ExecutionResult(response="Inventory batch not found.", success=False)

    if not batch:
        return ExecutionResult(response="Inventory batch not found.", success=False)
    
    # SECURITY: Validate user has access to the batch's restaurant
    restaurants = _get_user_restaurants(db, user.id)
    if not any(r["id"] == str(batch.restaurant_id) for r in restaurants):
        return ExecutionResult(response="Inventory batch not found.", success=False)

    try:
        qty = Decimal(str(quantity))
        if qty <= 0:
            return ExecutionResult(response="Quantity must be positive.", success=False)

        if batch.quantity < qty:
            return ExecutionResult(
                response=f"Insufficient quantity. Current: {batch.quantity} {batch.unit}",
                success=False,
            )

        movement = InventoryMovements(
            batch_id=uuid.UUID(batch_id),
            movement_type=reason.lower()
            if reason.lower() in ("usage", "waste", "expired", "transfer")
            else "usage",
            quantity=qty,
            reason=reason,
            recorded_by_user_id=user.id,
        )
        db.add(movement)

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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return ExecutionResult(response=responses.ERROR_NO_OUTLETS, success=False)
        return ExecutionResult(
            response=responses.outlet_select_prompt(restaurants),
            success=False,
        )

    from app.db.models.inventory_locations import InventoryLocations

    locations = db.scalars(
        select(InventoryLocations)
        .where(InventoryLocations.restaurant_id == uuid.UUID(restaurant_id))
        .order_by(InventoryLocations.name.asc())
    ).all()

    formatted = [
        {"id": str(loc.id), "name": loc.name, "type": loc.location_type}
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
    restaurant_id = _get_restaurant_id(params, context, db, user.id)
    if not restaurant_id:
        return ExecutionResult(response="Please specify which outlet.", success=False)

    if not _is_restaurant_owner(db, user.id, uuid.UUID(restaurant_id)):
        return ExecutionResult(
            response="Only outlet owners can add storage locations.", success=False
        )

    name_raw = params.get("name")
    name = str(name_raw).strip() if name_raw else ""
    if not name:
        return ExecutionResult(
            response="Please provide a name for the location.", success=False
        )

    from app.db.models.inventory_locations import InventoryLocations

    try:
        location = InventoryLocations(
            restaurant_id=uuid.UUID(restaurant_id),
            name=name,
            location_type="general",
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
    staging_id = params.get("staging_id")
    if not staging_id:
        return ExecutionResult(
            response="Nothing to confirm. Upload a file first.", success=False
        )

    try:
        staging = db.get(FileProcessingStaging, uuid.UUID(staging_id))
    except ValueError:
        return ExecutionResult(response="Invalid staging ID.", success=False)

    if not staging:
        return ExecutionResult(response="Upload not found.", success=False)
    
    # SECURITY: Validate user owns this staging record
    if staging.user_id != user.id:
        return ExecutionResult(response="Upload not found.", success=False)

    if staging.status != "pending_review":
        return ExecutionResult(
            response=f"This upload is already {staging.status}.", success=False
        )

    from app.ai.db_tools.file_processing import create_file_processing_tools

    tools = create_file_processing_tools(
        db=db,
        user_id=user.id,
        actor_role="owner",
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
