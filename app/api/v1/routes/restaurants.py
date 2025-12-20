import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.api.v1.routes.auth import get_current_user
from app.core.config import Settings
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService
from app.schemas.invite import InviteCreate, InviteRead
from app.schemas.restaurant import (
    RestaurantCreate,
    RestaurantMemberRead,
    RestaurantMembershipRead,
    RestaurantRead,
)

router = APIRouter()


@router.post("/restaurants", response_model=RestaurantRead, status_code=status.HTTP_201_CREATED)
def create_restaurant(
    payload: RestaurantCreate,
    db: Session = Depends(get_db_dep),
    current_user: User = Depends(get_current_user),
) -> RestaurantRead:
    restaurant = RestaurantService(db).create_restaurant(
        owner_user_id=current_user.id, name=payload.name
    )
    return RestaurantRead.model_validate(restaurant)


@router.get("/restaurants", response_model=list[RestaurantMembershipRead])
def list_restaurants(
    db: Session = Depends(get_db_dep),
    current_user: User = Depends(get_current_user),
) -> list[RestaurantMembershipRead]:
    rows = RestaurantService(db).list_for_user(user_id=current_user.id)
    return [
        RestaurantMembershipRead(
            restaurant=RestaurantRead.model_validate(restaurant),
            role=membership.role,
            status=membership.status,
        )
        for restaurant, membership in rows
    ]


@router.get("/restaurants/{restaurant_id}/members", response_model=list[RestaurantMemberRead])
def list_members(
    restaurant_id: uuid.UUID,
    db: Session = Depends(get_db_dep),
    current_user: User = Depends(get_current_user),
) -> list[RestaurantMemberRead]:
    is_member = db.scalar(
        select(RestaurantUser.id).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == current_user.id,
            RestaurantUser.status != "removed",
        )
    )
    if not is_member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    rows = RestaurantService(db).list_members(restaurant_id=restaurant_id)
    return [
        RestaurantMemberRead(
            user_id=user.id,
            telegram_id=user.telegram_id,
            full_name=user.full_name,
            username=user.username,
            role=membership.role,
            status=membership.status,
        )
        for user, membership in rows
    ]


@router.post("/restaurants/{restaurant_id}/invites", response_model=InviteRead)
def create_invite(
    restaurant_id: uuid.UUID,
    payload: InviteCreate,
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
    current_user: User = Depends(get_current_user),
) -> InviteRead:
    expires_at: dt.datetime | None = None
    if payload.expires_in_hours is not None:
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=payload.expires_in_hours)

    created_by_is_superuser = current_user.telegram_id in set(settings.telegram_superuser_ids)
    invite = InviteCodeService(db).create_invite_code(
        restaurant_id=restaurant_id,
        target_role=payload.target_role,
        created_by_user_id=current_user.id,
        created_by_is_superuser=created_by_is_superuser,
        expires_at=expires_at,
    )
    deep_link = InviteCodeService.deep_link(
        bot_username=settings.telegram_bot_username, code=invite.code
    )
    return InviteRead(
        code=invite.code,
        restaurant_id=invite.restaurant_id,
        role=invite.role,
        expires_at=invite.expires_at,
        deep_link=deep_link,
    )
