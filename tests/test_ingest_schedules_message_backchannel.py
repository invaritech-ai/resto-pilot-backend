import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.user import User
from app.telegram.ingest import ingest_update


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


def test_ingest_schedules_message_backchannel_when_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    from app.workers import tasks as worker_tasks

    scheduled: dict[str, object] = {}

    def _fake_apply_async(*, args=None, kwargs=None, **options):
        scheduled["kwargs"] = dict(kwargs or {})
        scheduled["countdown"] = float(options.get("countdown", 0.0))
        return object()

    monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", _fake_apply_async)
    monkeypatch.setattr(worker_tasks.flush_session, "apply_async", lambda *a, **k: object())

    # Force "not skipped" path.
    monkeypatch.setattr("app.telegram.ingest.random.random", lambda: 0.9)

    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=10))
        db.commit()

        session_id = ingest_update(
            update=_make_update(
                update_id=1,
                message_id=1,
                chat_id=10,
                telegram_id=1,
                text="hello",
            ),
            session=db,
            settings=settings,
            schedule_flush=True,
        )

        assert session_id is not None
        assert scheduled["kwargs"] == {"session_id": str(session_id)}
        assert scheduled["countdown"] == 0.0


def test_ingest_skips_message_backchannel_when_probability_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    from app.workers import tasks as worker_tasks

    def _fail_apply_async(*_a, **_k):
        raise AssertionError("send_message_backchannel should not be scheduled")

    monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", _fail_apply_async)
    monkeypatch.setattr(worker_tasks.flush_session, "apply_async", lambda *a, **k: object())

    # Force "skipped" path.
    monkeypatch.setattr("app.telegram.ingest.random.random", lambda: 0.0)

    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=10))
        db.commit()

        session_id = ingest_update(
            update=_make_update(
                update_id=1,
                message_id=1,
                chat_id=10,
                telegram_id=1,
                text="hello",
            ),
            session=db,
            settings=settings,
            schedule_flush=True,
        )

        assert session_id is not None

