#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_dep, get_settings_dep
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
from app.main import create_app


def _seed_demo_data(db: Session, *, telegram_id: int, chat_id: int) -> None:
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, chat_id=chat_id, state="IDLE")
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

    product = Products(restaurant_id=restaurant.id, name_en="Pork Belly", is_active=True)
    db.add(product)
    db.flush()

    item = SupplierItems(
        supplier_id=supplier.id,
        product_id=None,
        supplier_sku="PORK-BELLY-1KG",
        supplier_name_raw="Pork Belly (skin-on) 1kg",
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


def _make_update(*, update_id: int, message_id: int, chat_id: int, telegram_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(dt.datetime.now(dt.UTC).timestamp()),
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "Demo"},
            "text": text,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run Telegram webhook locally (no Celery, no Telegram network).")
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        help="Incoming Telegram text (repeatable).",
    )
    parser.add_argument("--telegram-id", type=int, default=111, help="Telegram user id.")
    parser.add_argument("--chat-id", type=int, default=222, help="Telegram chat id.")
    parser.add_argument("--secret", default="secret", help="Webhook secret to use.")
    args = parser.parse_args()
    texts = args.text or ["search item pork"]

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    settings = Settings(
        debug=True,
        telegram_bot_token=os.getenv("APP_TELEGRAM_BOT_TOKEN", "test-token"),
        telegram_webhook_secret_token=args.secret,
        celery_broker_url="redis://localhost",
        openai_api_key="test",  # not used in this dry-run
        openai_base_url="http://localhost",  # not used in this dry-run
    )

    def _get_db_override() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_db_dep] = _get_db_override
    client = TestClient(app)

    with Session(engine) as db:
        _seed_demo_data(db, telegram_id=args.telegram_id, chat_id=args.chat_id)

    # Patch outgoing Telegram sends to stdout
    sent: list[str] = []

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> int | None:
        sent.append(text)
        return 1

    import app.telegram.bot_api as bot_api_mod
    import app.telegram.handler as handler_mod
    import app.conversation.processor as processor_mod

    # Patch all call sites that import send_message directly.
    bot_api_mod.send_message = _fake_send_message  # type: ignore[assignment]
    handler_mod.send_message = _fake_send_message  # type: ignore[assignment]
    processor_mod.send_message = _fake_send_message  # type: ignore[assignment]

    # Patch LLM parse step to avoid network and keep it deterministic
    from app.ai.item_search_query_planner import ItemSearchQueryPlan
    from app.domain.services.item_search_service import extract_nlp_hints

    def _fake_plan_item_search(*, semantic_text: str, settings: Settings):
        cleaned = semantic_text.strip()
        outlet_hint, supplier_hint = extract_nlp_hints(cleaned)
        for hint in (outlet_hint, supplier_hint):
            if hint:
                cleaned = cleaned.replace(hint, "")
        cleaned = cleaned.replace("for", " ").replace("from", " ")
        cleaned = " ".join(cleaned.split()).strip()
        return (
            ItemSearchQueryPlan(
                raw=cleaned,
                queries=[cleaned or "pork"],
                include_terms=[],
                exclude_terms=[],
                category_hints=[],
                outlet=None,
                supplier=None,
                status="active",
                limit=5,
            ),
            None,
            {},
            0,
        )

    import app.conversation.item_search_router as router_mod

    router_mod.plan_item_search = _fake_plan_item_search  # type: ignore[assignment]

    # Patch Celery enqueue to run inline
    import app.api.v1.routes.telegram as telegram_route
    from app.telegram.handler import handle_update_v2

    class _InlineTask:
        def delay(self, update: dict) -> object:
            with Session(engine) as db:
                handle_update_v2(update=update, db=db, settings=settings)
                db.commit()
            return type("R", (), {"id": "inline"})()

    telegram_route.handle_telegram_update = _InlineTask()  # type: ignore[assignment]

    sent_cursor = 0
    for i, text in enumerate(texts, 1):
        update = _make_update(
            update_id=i,
            message_id=i,
            chat_id=args.chat_id,
            telegram_id=args.telegram_id,
            text=text,
        )
        resp = client.post(
            "/api/v1/telegram",
            headers={"X-Telegram-Bot-Api-Secret-Token": args.secret},
            json=update,
        )
        print(f"\n=== Update {i} ===")
        print("Incoming:", text)
        print("HTTP:", resp.status_code, resp.json())

        new_msgs = sent[sent_cursor:]
        sent_cursor = len(sent)
        if new_msgs:
            print("\n--- Outgoing messages ---")
            for j, msg in enumerate(new_msgs, 1):
                print(f"\n[{j}]\n{msg}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
