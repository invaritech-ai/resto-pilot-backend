import datetime as dt
import re
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversation.processor import process_message_instant
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.inventory_batches import InventoryBatches
from app.db.models.products import Products
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.supplier_item_products import SupplierItemProducts
from app.db.models.supplier_items import SupplierItems
from app.db.models.supplier_prices import SupplierPrices
from app.db.models.suppliers import Suppliers
from app.db.models.user import User


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.file_id = None
        self.file_kind = None


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def test_item_search_basic_list_and_pending_action(monkeypatch: pytest.MonkeyPatch) -> None:
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
        user = User(telegram_id=1, chat_id=10, state="IDLE")
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

        supplier = Suppliers(
            user_id=user.id,
            name="Fresh Farms",
            name_normalized="fresh farms",
            is_active=True,
        )
        db.add(supplier)
        db.flush()
        db.add(
            RestaurantSuppliers(
                restaurant_id=restaurant.id,
                supplier_id=supplier.id,
                status="active",
            )
        )

        product = Products(restaurant_id=restaurant.id, name_en="Roma Tomato", is_active=True)
        db.add(product)
        db.flush()

        item = SupplierItems(
            supplier_id=supplier.id,
            product_id=None,
            supplier_sku="SKU-1",
            supplier_name_raw="Tomato Roma 1kg",
            pack_size_text="1kg",
            unit_basis="kg",
            min_order_qty=1,
            status="active",
            source_document_id=None,
        )
        db.add(item)
        db.flush()

        db.add(
            SupplierItemProducts(
                supplier_item_id=item.id,
                restaurant_id=restaurant.id,
                product_id=product.id,
            )
        )

        db.add(
            SupplierPrices(
                supplier_item_id=item.id,
                price=12.5,
                currency="USD",
                price_type="standard",
                valid_from=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                valid_to=None,
                min_qty=None,
                source_document_id=None,
            )
        )

        db.add(
            InventoryBatches(
                restaurant_id=restaurant.id,
                product_id=product.id,
                supplier_id=supplier.id,
                invoice_line_item_id=None,
                quantity=5,
                unit="kg",
                unit_cost=10,
                received_date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                expiry_date=None,
                location_id=None,
                status="available",
            )
        )

        db.commit()

        from app.ai.item_search_query_planner import ItemSearchQueryPlan

        def _fake_plan(*, semantic_text: str, settings: Settings):
            plan = ItemSearchQueryPlan(
                raw=semantic_text,
                queries=["tomato"],
                include_terms=[],
                exclude_terms=[],
                category_hints=[],
                outlet=None,
                supplier=None,
                status="active",
                limit=5,
            )
            return plan, None, {}, 0

        monkeypatch.setattr("app.conversation.item_search_router.plan_item_search", _fake_plan)

        result = process_message_instant(
            db=db,
            user=user,
            messages=[_Msg("search item tomato")],
            settings=settings,
            session_id=None,
            history=None,
        )

        assert "Items" in result.response_text
        assert "1." in result.response_text
        assert 'Reply "open 1"' in result.response_text
        assert UUID_RE.search(result.response_text) is None

        db.refresh(user)
        assert isinstance(user.state_data, dict)
        pending = user.state_data.get("pending_action")
        assert isinstance(pending, dict)
        assert pending.get("type") == "item_search"
        assert pending.get("scope") == "all"
        assert pending.get("restaurant_ids") == [str(restaurant.id)]
        assert pending.get("mode") == "search"
        last_page = pending.get("last_page")
        assert isinstance(last_page, list)
        assert last_page and isinstance(last_page[0], dict)
        assert last_page[0].get("restaurant_id") == str(restaurant.id)
