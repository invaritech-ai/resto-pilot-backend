from __future__ import annotations

import datetime as dt
import secrets
import string
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.domain.services.db_pending_action_service import DBPendingActionService
from app.schemas.db_action import DBAction, parse_db_action


def stage_cud_action(
    *,
    session: Session,
    user_id: uuid.UUID,
    action: DBAction,
    chat_id: int | None = None,
    session_id: uuid.UUID | None = None,
    expires_at: dt.datetime | None = None,
):
    service = DBPendingActionService(session)
    return service.create_pending(
        user_id=user_id,
        chat_id=chat_id,
        session_id=session_id,
        action_json=action.model_dump(),
        expires_at=expires_at,
    )


def execute_confirmed_action(
    *,
    session: Session,
    pending_action,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    if pending_action.status != "confirmed":
        raise ValueError("pending_action_not_confirmed")
    if pending_action.expires_at <= now:
        raise ValueError("pending_action_expired")

    action, errors = parse_db_action(pending_action.action_json)
    if action is None:
        raise ValueError(f"pending_action_invalid: {errors}")

    if action.crud == "create":
        return _execute_create(session=session, action=action)
    if action.crud == "update":
        return _execute_update(session=session, action=action)
    if action.crud == "delete":
        return _execute_delete(session=session, action=action)
    raise ValueError("unsupported_crud")


def _execute_create(*, session: Session, action: DBAction) -> dict[str, Any]:
    values = action.values or {}
    
    # Check for duplicates before creating
    duplicate_info = _check_duplicate(session=session, action=action, values=values)
    if duplicate_info:
        raise ValueError(f"duplicate_record: {duplicate_info}")
    
    if action.table == "restaurant_users":
        row = RestaurantUser(**(values))
    elif action.table == "invite_codes":
        # Auto-generate invite code if not provided
        if "code" not in values or not values["code"]:
            values["code"] = _generate_invite_code(session=session, code_length=10)
        
        # Set default expires_at to 30 days from now if not provided
        if "expires_at" not in values or not values["expires_at"]:
            values["expires_at"] = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
        
        row = InviteCodes(**(values))
    elif action.table == "restaurants":
        row = Restaurant(**(values))
    elif action.table == "users":
        row = User(**(values))
    else:
        raise ValueError("unsupported_table")

    session.add(row)
    session.commit()
    session.refresh(row)
    return {"status": "created", "id": getattr(row, "id")}


def _check_duplicate(
    *, session: Session, action: DBAction, values: dict[str, Any]
) -> str | None:
    """
    Check for existing records that would conflict with the create operation.
    Returns error message if duplicate found, None otherwise.
    """
    if action.table == "restaurants":
        # Check by restaurant_code (unique constraint)
        if "restaurant_code" in values:
            existing = session.scalar(
                select(Restaurant).where(Restaurant.restaurant_code == values["restaurant_code"])
            )
            if existing:
                return f"Restaurant with code '{values['restaurant_code']}' already exists"
        
        # Check by name + owner_user_id (business logic - same owner can't have duplicate names)
        if "name" in values and "owner_user_id" in values:
            existing = session.scalar(
                select(Restaurant).where(
                    Restaurant.name == values["name"],
                    Restaurant.owner_user_id == values["owner_user_id"],
                )
            )
            if existing:
                return f"Restaurant '{values['name']}' already exists for this owner"
    
    elif action.table == "restaurant_users":
        # Check by restaurant_id + user_id (unique constraint)
        if "restaurant_id" in values and "user_id" in values:
            existing = session.scalar(
                select(RestaurantUser).where(
                    RestaurantUser.restaurant_id == values["restaurant_id"],
                    RestaurantUser.user_id == values["user_id"],
                )
            )
            if existing:
                return f"User is already a member of this restaurant"
    
    elif action.table == "invite_codes":
        # Check by code (unique constraint)
        if "code" in values:
            existing = session.scalar(
                select(InviteCodes).where(InviteCodes.code == values["code"])
            )
            if existing:
                return f"Invite code '{values['code']}' already exists"
    
    elif action.table == "users":
        # Check by telegram_id (unique constraint)
        if "telegram_id" in values:
            existing = session.scalar(
                select(User).where(User.telegram_id == values["telegram_id"])
            )
            if existing:
                return f"User with telegram_id '{values['telegram_id']}' already exists"
    
    return None


def _generate_invite_code(*, session: Session, code_length: int = 10) -> str:
    """
    Generate a unique invite code.
    
    Args:
        session: Database session
        code_length: Length of the code (default 10)
    
    Returns:
        Unique invite code string
    """
    if code_length < 8 or code_length > 10:
        code_length = 10
    
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):  # Try up to 20 times
        code = "".join(secrets.choice(alphabet) for _ in range(code_length))
        
        # Check if code already exists
        existing = session.scalar(
            select(InviteCodes).where(InviteCodes.code == code)
        )
        if existing is None:
            return code
    
    raise RuntimeError("Failed to generate a unique invite code")


def _execute_update(*, session: Session, action: DBAction) -> dict[str, Any]:
    values = action.values or {}
    if action.table == "users":
        stmt = select(User)
        if action.filters and action.filters.get("by_user_id"):
            stmt = stmt.where(User.id == action.filters["by_user_id"])
        row = session.scalar(stmt)
    elif action.table == "restaurants":
        stmt = select(Restaurant)
        if action.filters and action.filters.get("by_restaurant_id"):
            stmt = stmt.where(Restaurant.id == action.filters["by_restaurant_id"])
        row = session.scalar(stmt)
    elif action.table == "restaurant_users":
        stmt = select(RestaurantUser)
        if action.filters and action.filters.get("by_restaurant_user_id"):
            stmt = stmt.where(RestaurantUser.id == action.filters["by_restaurant_user_id"])
        row = session.scalar(stmt)
    elif action.table == "invite_codes":
        stmt = select(InviteCodes)
        if action.filters and action.filters.get("by_invite_code"):
            stmt = stmt.where(InviteCodes.code == action.filters["by_invite_code"])
        row = session.scalar(stmt)
    else:
        raise ValueError("unsupported_table")

    if row is None:
        raise ValueError("record_not_found")

    for key, value in values.items():
        if hasattr(row, key):
            setattr(row, key, value)

    session.add(row)
    session.commit()
    return {"status": "updated", "id": getattr(row, "id")}


def _execute_delete(*, session: Session, action: DBAction) -> dict[str, Any]:
    if action.table == "restaurant_users":
        stmt = select(RestaurantUser)
        if action.filters and action.filters.get("by_restaurant_user_id"):
            stmt = stmt.where(RestaurantUser.id == action.filters["by_restaurant_user_id"])
        row = session.scalar(stmt)
    elif action.table == "invite_codes":
        stmt = select(InviteCodes)
        if action.filters and action.filters.get("by_invite_code"):
            stmt = stmt.where(InviteCodes.code == action.filters["by_invite_code"])
        row = session.scalar(stmt)
    else:
        raise ValueError("delete_not_supported_for_table")

    if row is None:
        raise ValueError("record_not_found")

    session.delete(row)
    session.commit()
    return {"status": "deleted", "id": getattr(row, "id")}
