from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.schemas.db_action import DBAction


def execute_read_action(*, session: Session, action: DBAction) -> list[dict[str, Any]]:
    model = _resolve_model(action.table)
    stmt = select(model)
    stmt = _apply_filters(stmt=stmt, action=action)

    rows = list(session.scalars(stmt))
    return [_row_to_dict(row=row, columns=action.columns or []) for row in rows]


def _resolve_model(table: str):
    if table == "users":
        return User
    if table == "restaurants":
        return Restaurant
    if table == "restaurant_users":
        return RestaurantUser
    if table == "invite_codes":
        return InviteCodes
    raise ValueError(f"Unsupported table: {table}")


def _apply_filters(*, stmt, action: DBAction):
    filters = action.filters or {}
    table = action.table

    if table == "users":
        user_id = filters.get("by_user_id")
        if user_id:
            stmt = stmt.where(User.id == user_id)
    elif table == "restaurants":
        restaurant_id = filters.get("by_restaurant_id")
        if restaurant_id:
            stmt = stmt.where(Restaurant.id == restaurant_id)
    elif table == "restaurant_users":
        restaurant_id = filters.get("by_restaurant_id")
        if restaurant_id:
            stmt = stmt.where(RestaurantUser.restaurant_id == restaurant_id)
        user_id = filters.get("by_user_id")
        if user_id:
            stmt = stmt.where(RestaurantUser.user_id == user_id)
    elif table == "invite_codes":
        restaurant_id = filters.get("by_restaurant_id")
        if restaurant_id:
            stmt = stmt.where(InviteCodes.restaurant_id == restaurant_id)
        code = filters.get("by_invite_code")
        if code:
            stmt = stmt.where(InviteCodes.code == code)
    return stmt


def _row_to_dict(*, row: object, columns: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        if hasattr(row, col):
            out[col] = getattr(row, col)
    return out
