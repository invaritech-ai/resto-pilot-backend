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


def test_link_supplier_to_outlets_requires_confirmation() -> None:
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

        merc = Restaurant(
            name="Mercato",
            restaurant_code="MERC",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        ktm = Restaurant(
            name="KTM",
            restaurant_code="KTM",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        db.add_all([merc, ktm])
        db.flush()
        db.add_all(
            [
                RestaurantUser(restaurant_id=merc.id, user_id=owner.id, role="owner", status="active"),
                RestaurantUser(restaurant_id=ktm.id, user_id=owner.id, role="owner", status="active"),
            ]
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(merc.id): "owner", str(ktm.id): "owner"},
            pending_action=None,
            user_message="link supplier Fresh Farms for Mercato, KTM",
        )
        raw = tools["link_suppliers"].handler(
            {"supplier_name": "Fresh Farms", "restaurant_names": ["Mercato", "KTM"]}
        )
        payload = json.loads(raw)
        assert payload["status"] == "needs_confirmation"
        assert "pending_action" in payload["context_update"]
