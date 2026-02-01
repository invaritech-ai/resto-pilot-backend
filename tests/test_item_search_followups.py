import datetime as dt
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversation.processor import process_message_instant
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.products import Products
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.supplier_item_products import SupplierItemProducts
from app.db.models.supplier_items import SupplierItems
from app.db.models.suppliers import Suppliers
from app.db.models.user import User


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.file_id = None
        self.file_kind = None


def test_item_search_more_and_open_are_no_parse_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_bot_token="test-token",
        celery_broker_url="redis://localhost",
    )

    with Session(engine) as db:
        user = User(telegram_id=1, chat_id=10, state="IDLE", state_data=None)
        db.add(user)
        db.flush()

        restaurant = Restaurant(
            name="Mercato",
            restaurant_code="MERC1234",
            owner_user_id=user.id,
            onboarding_status={},
        )
        db.add(restaurant)
        db.flush()
        db.add(
            RestaurantUser(
                restaurant_id=restaurant.id,
                user_id=user.id,
                role="owner",
                status="active",
                invited_by=None,
            )
        )

        supplier = Suppliers(user_id=user.id, name="Fresh Farms", is_active=True)
        db.add(supplier)
        db.flush()
        db.add(RestaurantSuppliers(restaurant_id=restaurant.id, supplier_id=supplier.id, status="active"))

        product = Products(restaurant_id=restaurant.id, name_en="Tomato", is_active=True)
        db.add(product)
        db.flush()

        items = []
        for i in range(7):
            it = SupplierItems(
                supplier_id=supplier.id,
                product_id=None,
                supplier_sku=f"SKU-{i}",
                supplier_name_raw=f"Tomato {i}",
                pack_size_text=None,
                unit_basis=None,
                min_order_qty=None,
                status="active",
                source_document_id=None,
            )
            db.add(it)
            db.flush()
            db.add(SupplierItemProducts(supplier_item_id=it.id, restaurant_id=restaurant.id, product_id=product.id))
            items.append(it)

        db.commit()

        created_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
        user.state_data = {
            "active_restaurant_id": str(restaurant.id),
            "pending_action": {
                "type": "item_search",
                "mode": "search",
                "query_plan": {"raw": "tomato", "queries": ["tomato"]},
                "restaurant_id": str(restaurant.id),
                "supplier_id": None,
                "status": "active",
                "limit": 5,
                "offset": 0,
                "last_page": [
                    {
                        "supplier_item_id": str(items[i].id),
                        "label": f"Tomato {i}",
                        "supplier_id": str(supplier.id),
                        "supplier_name": supplier.name,
                    }
                    for i in range(5)
                ],
                "has_more": True,
                "created_at": created_at,
            },
        }
        db.add(user)
        db.commit()

        def _should_not_call(*args, **kwargs):
            raise AssertionError("plan_item_search should not be called for follow-ups")

        monkeypatch.setattr("app.conversation.item_search_router.plan_item_search", _should_not_call)

        more_result = process_message_instant(
            db=db,
            user=user,
            messages=[_Msg("more")],
            settings=settings,
            session_id=None,
            history=None,
        )
        assert "Items" in more_result.response_text
        assert "1." in more_result.response_text

        open_result = process_message_instant(
            db=db,
            user=user,
            messages=[_Msg("open 1")],
            settings=settings,
            session_id=None,
            history=None,
        )
        assert "Supplier:" in open_result.response_text

