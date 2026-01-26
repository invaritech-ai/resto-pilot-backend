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


def test_link_supplier_to_all_outlets_requires_confirmation() -> None:
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

        a = Restaurant(
            name="A",
            restaurant_code="A",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        b = Restaurant(
            name="B",
            restaurant_code="B",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        db.add_all([a, b])
        db.flush()

        db.add_all(
            [
                RestaurantUser(restaurant_id=a.id, user_id=owner.id, role="owner", status="active"),
                RestaurantUser(restaurant_id=b.id, user_id=owner.id, role="owner", status="active"),
            ]
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(a.id): "owner", str(b.id): "owner"},
            pending_action=None,
            user_message="Add supplier Cheong Hing to all my outlets",
        )
        raw = tools["link_supplier_to_all_outlets"].handler({"supplier_name": "Cheong Hing"})
        payload = json.loads(raw)
        assert payload["status"] == "needs_confirmation"
        assert "pending_action" in payload["context_update"]

