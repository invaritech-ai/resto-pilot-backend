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
from app.db.models.telegram_messages import TelegramMessages
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


def test_batching_webhook_persists_messages_and_seals_on_done(
    monkeypatch: pytest.MonkeyPatch,
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

    scheduled_flushes: list[tuple[dict, float]] = []
    enqueued: dict[str, list[dict]] = {"process": []}

    def _fake_apply_async(*, args=None, kwargs=None, **options):
        scheduled_flushes.append((dict(kwargs or {}), float(options.get("countdown", 0.0))))
        return type("R", (), {"id": "fake"})()

    def _fake_process_delay(*, session_id: str):
        enqueued["process"].append({"session_id": session_id})

    monkeypatch.setattr(worker_tasks.flush_session, "apply_async", _fake_apply_async)
    monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", lambda *a, **k: object())
    monkeypatch.setattr(worker_tasks.process_session, "delay", _fake_process_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=1, text="Hi"),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(scheduled_flushes) == 1

    with Session(engine) as db:
        session_row = db.scalar(
            select(TelegramSessions).where(
                TelegramSessions.chat_id == 10, TelegramSessions.status == "open"
            )
        )
        assert session_row is not None
        session_id = session_row.id
        messages = list(
            db.scalars(
                select(TelegramMessages).where(TelegramMessages.session_id == session_id)
            )
        )
        assert len(messages) == 1

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(
            update_id=2, message_id=2, chat_id=10, telegram_id=1, text="/done"
        ),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(enqueued["process"]) == 1
    assert enqueued["process"][0]["session_id"] == str(session_id)

    with Session(engine) as db:
        sealed = db.scalar(select(TelegramSessions).where(TelegramSessions.id == session_id))
        assert sealed is not None
        assert sealed.status == "processing"
        assert sealed.closed_at is not None

        messages = list(
            db.scalars(
                select(TelegramMessages).where(TelegramMessages.session_id == session_id)
            )
        )
        assert len(messages) == 2

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json=_make_update(update_id=3, message_id=3, chat_id=10, telegram_id=1, text="Next"),
    )
    assert resp.status_code == 200

    with Session(engine) as db:
        open_sessions = list(
            db.scalars(
                select(TelegramSessions).where(
                    TelegramSessions.chat_id == 10, TelegramSessions.status == "open"
                )
            )
        )
        assert len(open_sessions) == 1
        assert open_sessions[0].id != session_id
