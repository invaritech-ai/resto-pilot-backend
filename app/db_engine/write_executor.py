from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
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
    if action.table == "restaurant_users":
        row = RestaurantUser(**(action.values or {}))
    elif action.table == "invite_codes":
        row = InviteCodes(**(action.values or {}))
    elif action.table == "restaurants":
        row = Restaurant(**(action.values or {}))
    elif action.table == "users":
        row = User(**(action.values or {}))
    else:
        raise ValueError("unsupported_table")

    session.add(row)
    session.commit()
    session.refresh(row)
    return {"status": "created", "id": getattr(row, "id")}


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
