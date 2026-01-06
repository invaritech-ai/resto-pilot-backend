import datetime as dt
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.user import User
from app.main import create_app


def _make_update(
    *,
    update_id: int,
    message_id: int,
    chat_id: int,
    telegram_id: int,
    text: str,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()),
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "A"},
            "text": text,
        },
    }


def test_webhook_routes_start_to_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_webhook_secret_token="secret",
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    def _get_db_override() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_db_dep] = _get_db_override
    client = TestClient(app)

    from app.workers import tasks as worker_tasks

    enqueued: dict[str, object] = {}

    def _fake_delay(*args, **kwargs):
        enqueued["called"] = True
        return type("R", (), {"id": "fake"})()

    monkeypatch.setattr(worker_tasks.handle_telegram_update, "delay", _fake_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=1, text="/start"),
    )
    assert resp.status_code == 200
    assert enqueued.get("called") is True


def test_webhook_routes_instant_stateful(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_webhook_secret_token="secret",
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    def _get_db_override() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_db_dep] = _get_db_override
    client = TestClient(app)

    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=10, state="COLLECT_PHONE"))
        db.commit()

    from app.workers import tasks as worker_tasks

    enqueued: dict[str, object] = {}

    def _fake_delay(*args, **kwargs):
        enqueued["called"] = True
        return type("R", (), {"id": "fake"})()

    monkeypatch.setattr(worker_tasks.handle_telegram_update, "delay", _fake_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=1, text="123"),
    )
    assert resp.status_code == 200
    assert enqueued.get("called") is True


def test_webhook_routes_unregistered_user_to_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_webhook_secret_token="secret",
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    def _get_db_override() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_db_dep] = _get_db_override
    client = TestClient(app)

    from app.workers import tasks as worker_tasks

    enqueued: dict[str, object] = {}

    def _fake_delay(*args, **kwargs):
        enqueued["called"] = True
        return type("R", (), {"id": "fake"})()

    monkeypatch.setattr(worker_tasks.handle_telegram_update, "delay", _fake_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=999, text="Hello"),
    )
    assert resp.status_code == 200
    assert enqueued.get("called") is True

