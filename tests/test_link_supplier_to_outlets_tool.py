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


def test_link_supplier_to_outlets_settings_reply_executes() -> None:
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
            name="Mercato Downtown",
            restaurant_code="MERC",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        gup = Restaurant(
            name="Gupshup",
            restaurant_code="GUP",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        joy = Restaurant(
            name="Joyful banquet",
            restaurant_code="JOY",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        db.add_all([merc, gup, joy])
        db.flush()
        db.add_all(
            [
                RestaurantUser(restaurant_id=merc.id, user_id=owner.id, role="owner", status="active"),
                RestaurantUser(restaurant_id=gup.id, user_id=owner.id, role="owner", status="active"),
                RestaurantUser(restaurant_id=joy.id, user_id=owner.id, role="owner", status="active"),
            ]
        )
        db.commit()

        roles = {str(merc.id): "owner", str(gup.id): "owner", str(joy.id): "owner"}
        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles=roles,
            pending_action=None,
            user_message="link supplier Cheong Hing for Mercato Downtown, Gupshup, Joyful banquet",
        )
        raw = tools["link_suppliers"].handler(
            {
                "supplier_name": "Cheong Hing Company",
                "restaurant_names": ["Mercato Downtown", "Gupshup", "Joyful banquet"],
            }
        )
        payload = json.loads(raw)
        assert payload["status"] == "needs_confirmation"
        pending_action = payload["context_update"]["pending_action"]

        tools2 = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles=roles,
            pending_action=pending_action,
            user_message="HKD, EN, 2 days",
        )
        raw2 = tools2["link_suppliers"].handler({})
        payload2 = json.loads(raw2)
        assert payload2["status"] == "ok"
        assert payload2["created_links"] == 3

        supplier_id = payload2["context_update"]["active_supplier_id"]
        assert supplier_id
        suppliers_raw = tools2["list_suppliers"].handler({"restaurant_id": str(gup.id)})
        suppliers_payload = json.loads(suppliers_raw)
        assert [s["name"] for s in suppliers_payload["suppliers"]] == ["Cheong Hing Company"]
