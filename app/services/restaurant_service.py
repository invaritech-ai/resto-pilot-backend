from __future__ import annotations

import secrets
import string
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User


class RestaurantService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_restaurant(
        self,
        *,
        owner_user_id: uuid.UUID,
        name: str,
        code_length: int = 8,
    ) -> Restaurant:
        name = name.strip()
        if not name:
            raise ValueError("Restaurant name is required")
        if code_length < 6 or code_length > 12:
            raise ValueError("code_length must be 6..12")

        alphabet = string.ascii_uppercase + string.digits
        for _ in range(20):
            restaurant_code = "".join(secrets.choice(alphabet) for _ in range(code_length))
            restaurant = Restaurant(
                name=name,
                restaurant_code=restaurant_code,
                owner_user_id=owner_user_id,
            )
            self.session.add(restaurant)
            self.session.flush()
            membership = RestaurantUser(
                restaurant_id=restaurant.id,
                user_id=owner_user_id,
                is_owner=True,
                is_active=True,
            )
            self.session.add(membership)
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                continue
            self.session.refresh(restaurant)
            return restaurant

        raise RuntimeError("Failed to generate a unique restaurant_code")

    def add_membership(
        self,
        *,
        restaurant_id: uuid.UUID,
        user_id: uuid.UUID,
        is_owner: bool = False,
    ) -> RestaurantUser:
        membership = RestaurantUser(
            restaurant_id=restaurant_id,
            user_id=user_id,
            is_owner=is_owner,
            is_active=True,
        )
        self.session.add(membership)
        self.session.commit()
        self.session.refresh(membership)
        return membership

    def user_membership_exists(
        self, *, restaurant_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        existing_id = self.session.scalar(
            select(RestaurantUser.id).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user_id,
                RestaurantUser.is_active.is_(True),
            )
        )
        return existing_id is not None

    def list_for_user(self, *, user_id: uuid.UUID) -> list[tuple[Restaurant, RestaurantUser]]:
        rows = self.session.execute(
            select(Restaurant, RestaurantUser)
            .join(RestaurantUser, RestaurantUser.restaurant_id == Restaurant.id)
            .where(
                RestaurantUser.user_id == user_id,
                RestaurantUser.is_active.is_(True),
            )
            .order_by(Restaurant.created_at.desc())
        ).all()
        return [(restaurant, membership) for restaurant, membership in rows]

    def list_members(self, *, restaurant_id: uuid.UUID) -> list[tuple[User, RestaurantUser]]:
        rows = self.session.execute(
            select(User, RestaurantUser)
            .join(RestaurantUser, RestaurantUser.user_id == User.id)
            .where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.is_active.is_(True),
            )
            .order_by(RestaurantUser.joined_at.asc())
        ).all()
        return [(user, membership) for user, membership in rows]
