import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.tool_resolver import resolve_with_tools
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.suppliers import Suppliers
from app.db.models.user import User


def test_list_suppliers_routes_to_list_my_suppliers() -> None:
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
            name="Joyful banquet",
            restaurant_code="JB",
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

        supplier = Suppliers(user_id=owner.id, name="Cheong Hing Company", is_active=True)
        db.add(supplier)
        db.flush()
        db.add(
            RestaurantSuppliers(
                restaurant_id=restaurant.id,
                supplier_id=supplier.id,
                status="active",
            )
        )
        db.commit()

        result = resolve_with_tools(
            db=db,
            user=owner,
            message_text="list suppliers",
            history=None,
            active_restaurant_id=str(restaurant.id),
            active_supplier_id=None,
            pending_action=None,
            settings=Settings(
                telegram_webhook_secret_token="secret",
                telegram_batching_enabled=True,
                celery_broker_url="redis://localhost",
                telegram_bot_token="test-token",
            ),
            chat_id=owner.chat_id,
            session_id=None,
        )

        payload = json.loads(result.response_text)
        assert result.tool_calls == 1
        assert result.llm_calls == []
        assert result.tool_names == ["list_my_suppliers"]
        assert [s["name"] for s in payload["suppliers"]] == ["Cheong Hing Company"]
