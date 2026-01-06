import datetime as dt
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.user import User
from app.main import create_app
from app.telegram.handler import handle_update


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
            "from": {"id": telegram_id, "first_name": "A", "last_name": "B"},
            "text": text,
        },
    }


def test_phone_intake_e2e_via_webhook_batching_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
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

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
        return None

    monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

    def _fail_ingest_update(*_args, **_kwargs):
        raise AssertionError("ingest_update should not be called for COLLECT_PHONE messages")

    monkeypatch.setattr("app.api.v1.routes.telegram.ingest_update", _fail_ingest_update)

    from app.workers import tasks as worker_tasks

    def _fake_handle_telegram_update_delay(update: dict):
        with Session(engine) as db:
            handle_update(update=update, db=db, settings=settings)
        return type("R", (), {"id": "fake"})()

    monkeypatch.setattr(worker_tasks.handle_telegram_update, "delay", _fake_handle_telegram_update_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=10, text="/start"),
    )
    assert resp.status_code == 200

    with Session(engine) as db:
        user = db.scalar(select(User).where(User.telegram_id == 10))
        assert user is not None
        assert user.phone is None
        assert user.state == "COLLECT_PHONE"

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(
            update_id=2, message_id=2, chat_id=10, telegram_id=10, text="+14155550101"
        ),
    )
    assert resp.status_code == 200

    with Session(engine) as db:
        user2 = db.scalar(select(User).where(User.telegram_id == 10))
        assert user2 is not None
        assert user2.phone == "+14155550101"
        assert user2.state == "IDLE"
        assert user2.is_phone_verified is False
