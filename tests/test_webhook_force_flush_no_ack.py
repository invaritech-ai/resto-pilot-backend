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
from app.db.models.telegram_session import TelegramSessions
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


@pytest.mark.parametrize("command", ["/done", "/respond"])
def test_webhook_force_flush_enqueues_process_without_session_ack(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
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
        db.add(User(telegram_id=1, chat_id=10))
        db.commit()

    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.flush_session, "apply_async", lambda *a, **k: object())
    monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", lambda *a, **k: object())

    enqueued: dict[str, object] = {}

    def _fake_process_delay(*, session_id: str):
        enqueued["process_session_id"] = session_id
        return object()

    monkeypatch.setattr(worker_tasks.process_session, "delay", _fake_process_delay)

    def _fail_ack_delay(*_args, **_kwargs):
        raise AssertionError("send_session_ack should not be enqueued for force-flush commands")

    monkeypatch.setattr(worker_tasks.send_session_ack, "delay", _fail_ack_delay)

    # First message creates an open session (batched path).
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=1, text="Hi"),
    )
    assert resp.status_code == 200

    with Session(engine) as db:
        open_session = db.scalar(
            select(TelegramSessions).where(
                TelegramSessions.chat_id == 10, TelegramSessions.status == "open"
            )
        )
        assert open_session is not None
        session_id = str(open_session.id)

    # Force-flush command should enqueue process_session and not ack.
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=2, message_id=2, chat_id=10, telegram_id=1, text=command),
    )
    assert resp.status_code == 200
    assert enqueued.get("process_session_id") == session_id

