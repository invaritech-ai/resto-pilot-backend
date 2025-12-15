import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService
from app.telegram.processor import process_update


def _make_settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        telegram_bot_username="MyBot",
        telegram_superuser_ids=[],
    )


def _make_update(*, chat_id: int, telegram_id: int, text: str) -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "Test", "username": "tester"},
            "text": text,
        }
    }


def test_owner_onboarding_no_invite_code_creates_restaurant_and_owner_membership():
    settings = _make_settings()
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        resp = process_update(
            update=_make_update(chat_id=10, telegram_id=100, text="/start"),
            session=session,
            settings=settings,
        )
        assert resp and resp["method"] == "sendMessage"

        user = session.scalar(select(User).where(User.telegram_id == 100))
        assert user is not None
        assert user.state == "AWAITING_RESTAURANT_NAME"

        process_update(
            update=_make_update(chat_id=10, telegram_id=100, text="Pasta Place"),
            session=session,
            settings=settings,
        )

        restaurants = session.scalars(select(Restaurant)).all()
        assert len(restaurants) == 1
        memberships = session.scalars(select(RestaurantUser)).all()
        assert len(memberships) == 1
        assert memberships[0].role == "owner"
        assert memberships[0].status == "active"
        assert memberships[0].restaurant_id == restaurants[0].id
        assert memberships[0].user_id == user.id

        user = session.scalar(select(User).where(User.telegram_id == 100))
        assert user is not None
        assert user.state == "IDLE"

        assert session.scalars(select(InviteCodes)).all() == []


def test_staff_invite_code_joins_existing_restaurant_and_marks_invite_used():
    settings = _make_settings()
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        owner_user = User(telegram_id=1, chat_id=1, state="IDLE")
        session.add(owner_user)
        session.commit()
        session.refresh(owner_user)

        restaurant_service = RestaurantService(session)
        restaurant = restaurant_service.create_restaurant(
            owner_user_id=owner_user.id, name="Cafe"
        )
        restaurant_service.add_membership(
            restaurant_id=restaurant.id,
            user_id=owner_user.id,
            role="owner",
            invited_by=None,
        )

        invite_service = InviteCodeService(session)
        invite = invite_service.create_invite_code(
            restaurant_id=restaurant.id,
            target_role="staff",
            created_by_user_id=owner_user.id,
            created_by_is_superuser=False,
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
            code_length=10,
        )

        process_update(
            update=_make_update(chat_id=20, telegram_id=200, text=f"/start {invite.code}"),
            session=session,
            settings=settings,
        )

        new_user = session.scalar(select(User).where(User.telegram_id == 200))
        assert new_user is not None

        membership = session.scalar(
            select(RestaurantUser).where(
                RestaurantUser.user_id == new_user.id,
                RestaurantUser.restaurant_id == restaurant.id,
            )
        )
        assert membership is not None
        assert membership.role == "staff"
        assert membership.status == "active"
        assert membership.invited_by == owner_user.id

        invite_after = session.get(InviteCodes, invite.id)
        assert invite_after is not None
        assert invite_after.used_at is not None

        assert session.scalars(select(Restaurant)).all() == [restaurant]

