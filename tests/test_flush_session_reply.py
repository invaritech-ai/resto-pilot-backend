import datetime as dt
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.telegram_session import TelegramSessions
from app.workers.tasks import flush_session


def test_flush_session_sends_ack_and_enqueues_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    expected = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

    with Session(engine) as db:
        session_row = TelegramSessions(
            chat_id=10,
            started_at=expected,
            last_activity_at=expected,
            flush_at=expected,
            status="open",
            hint_command=None,
            closed_at=None,
        )
        db.add(session_row)
        db.commit()

        session_id = str(session_row.id)

    @contextmanager
    def _fake_worker_db_session():
        with Session(engine) as db:
            yield db

    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks, "worker_db_session", _fake_worker_db_session)

    enqueued: dict[str, object] = {}

    def _fake_delay(*, session_id: str):
        enqueued["process_session_id"] = session_id
        return object()

    monkeypatch.setattr(worker_tasks.process_session, "delay", _fake_delay)

    flush_session(session_id=session_id, expected_last_activity_at=expected.isoformat())

    assert enqueued["process_session_id"] == session_id

    with Session(engine) as db:
        sealed = db.scalar(select(TelegramSessions).where(TelegramSessions.id == session_row.id))
        assert sealed is not None
        assert sealed.status == "processing"
        assert sealed.closed_at is not None
