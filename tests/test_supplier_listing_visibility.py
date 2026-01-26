import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.db_tools.suppliers import create_supplier_tools
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.suppliers import Suppliers
from app.db.models.user import User


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_list_suppliers_includes_legacy_null_user_supplier() -> None:
    engine = _make_engine()
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

        legacy_supplier = Suppliers(user_id=None, name="Fresh Farms", is_active=True)
        db.add(legacy_supplier)
        db.flush()

        db.add(
            RestaurantSuppliers(
                restaurant_id=restaurant.id,
                supplier_id=legacy_supplier.id,
                status="active",
            )
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(restaurant.id): "owner"},
        )
        payload = json.loads(
            tools["list_suppliers"].handler({"restaurant_id": str(restaurant.id)})
        )

        assert payload["restaurant_name"] == "Joyful banquet"
        assert [s["name"] for s in payload["suppliers"]] == ["Fresh Farms"]


def test_list_my_suppliers_includes_legacy_only_when_accessible() -> None:
    engine = _make_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        owner = User(telegram_id=1, chat_id=10)
        other = User(telegram_id=2, chat_id=20)
        db.add_all([owner, other])
        db.flush()

        restaurant_visible = Restaurant(
            name="Joyful banquet",
            restaurant_code="JB",
            owner_user_id=owner.id,
            onboarding_status={},
        )
        restaurant_hidden = Restaurant(
            name="Hidden outlet",
            restaurant_code="HO",
            owner_user_id=other.id,
            onboarding_status={},
        )
        db.add_all([restaurant_visible, restaurant_hidden])
        db.flush()

        db.add_all(
            [
                RestaurantUser(
                    restaurant_id=restaurant_visible.id,
                    user_id=owner.id,
                    role="owner",
                    status="active",
                ),
                RestaurantUser(
                    restaurant_id=restaurant_hidden.id,
                    user_id=other.id,
                    role="owner",
                    status="active",
                ),
            ]
        )

        legacy_supplier = Suppliers(user_id=None, name="Fresh Farms", is_active=True)
        owned_supplier = Suppliers(user_id=owner.id, name="My Vendor", is_active=True)
        db.add_all([legacy_supplier, owned_supplier])
        db.flush()

        db.add_all(
            [
                RestaurantSuppliers(
                    restaurant_id=restaurant_visible.id,
                    supplier_id=legacy_supplier.id,
                    status="active",
                ),
                RestaurantSuppliers(
                    restaurant_id=restaurant_hidden.id,
                    supplier_id=legacy_supplier.id,
                    status="active",
                ),
            ]
        )
        db.commit()

        tools = create_supplier_tools(
            db=db,
            user_id=owner.id,
            actor_role="owner",
            restaurant_roles={str(restaurant_visible.id): "owner"},
        )
        payload = json.loads(tools["list_my_suppliers"].handler({}))

        suppliers_by_name = {s["name"]: s for s in payload["suppliers"]}
        assert set(suppliers_by_name.keys()) == {"Fresh Farms", "My Vendor"}
        assert suppliers_by_name["My Vendor"]["linked_outlets"] == []
        assert [o["restaurant_name"] for o in suppliers_by_name["Fresh Farms"]["linked_outlets"]] == [
            "Joyful banquet"
        ]

