import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.db_tools.suppliers import create_supplier_tools
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User


def test_create_supplier_confirmation_does_not_keyerror() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        owner = User(telegram_id=1, chat_id=10)
        db.add(owner)
        db.flush()

        restaurant = Restaurant(
            name="KTM",
            restaurant_code="KTM",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        db.add(restaurant)
        db.flush()
        db.add(
            RestaurantUser(
                restaurant_id=restaurant.id,
                user_id=owner.id,
                role="owner",
                status="active",
            )
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(restaurant.id): "owner"},
            pending_action=None,
            user_message="add supplier Cheong Hing to KTM",
        )
        raw = tools["create_supplier"].handler(
            {"restaurant_id": str(restaurant.id), "name": "Cheong Hing"}
        )
        payload = json.loads(raw)
        assert payload["status"] == "pending_confirmation"
        assert "KTM" in payload["message"]

