import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep
from app.api.v1.routes.auth import get_current_user
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.domain.services.restaurant_service import RestaurantService
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
            is_owner=membership.is_owner,
            is_active=membership.is_active,
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
            RestaurantUser.is_active.is_(True),
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
            is_owner=membership.is_owner,
            is_active=membership.is_active,
        )
        for user, membership in rows
    ]
