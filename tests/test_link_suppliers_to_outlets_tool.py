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


def test_link_suppliers_to_outlets_requires_confirmation() -> None:
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

        x = Restaurant(
            name="XYZ",
            restaurant_code="XYZ",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        s = Restaurant(
            name="STU",
            restaurant_code="STU",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        db.add_all([x, s])
        db.flush()
        db.add_all(
            [
                RestaurantUser(restaurant_id=x.id, user_id=owner.id, role="owner", status="active"),
                RestaurantUser(restaurant_id=s.id, user_id=owner.id, role="owner", status="active"),
            ]
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(x.id): "owner", str(s.id): "owner"},
            pending_action=None,
            user_message="link supplier ABC, DEF to XYZ, STU",
        )
        raw = tools["link_suppliers"].handler(
            {
                "supplier_names": ["ABC", "DEF"],
                "restaurant_names": ["XYZ", "STU"],
            }
        )
        payload = json.loads(raw)
        assert payload["status"] == "needs_confirmation"
        assert "pending_action" in payload["context_update"]
