import datetime as dt
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.openai_client import OpenAIError
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User


def test_process_session_schedules_retry_on_openai_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    started = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

    with Session(engine) as db:
        user = User(telegram_id=1, chat_id=10)
        db.add(user)
        db.commit()

        session_row = TelegramSessions(
            chat_id=10,
            started_at=started,
            last_activity_at=started,
            flush_at=started,
            status="processing",
            hint_command=None,
            closed_at=None,
        )
        db.add(session_row)
        db.commit()

        db.add(
            TelegramMessages(
                session_id=session_row.id,
                chat_id=10,
                user_id=user.id,
                telegram_id=user.telegram_id,
                message_id=1,
                update_id=1001,
                received_at=started,
                text="What time is it?",
                caption=None,
                file_id=None,
                file_unique_id=None,
                file_kind=None,
                mime=None,
                filename=None,
                size=None,
            )
        )
        db.commit()

        session_id = str(session_row.id)

    @contextmanager
    def _fake_worker_db_session():
        with Session(engine) as db:
            yield db

    from app.processing import session_processor
    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(session_processor, "worker_db_session", _fake_worker_db_session)
    monkeypatch.setattr(worker_tasks, "worker_db_session", _fake_worker_db_session)
    monkeypatch.setattr(
        session_processor,
        "get_settings",
        lambda: Settings(telegram_bot_token="test", openai_api_key="test"),
    )

    def _fail_generate_session_reply(*, messages, hint_command, settings):
        raise OpenAIError("transient")

    monkeypatch.setattr(session_processor, "generate_session_reply", _fail_generate_session_reply)

    scheduled: dict[str, object] = {}

    def _fake_apply_async(*, args=None, kwargs=None, **options):
        scheduled["kwargs"] = dict(kwargs or {})
        scheduled["countdown"] = options.get("countdown")
        return object()

    monkeypatch.setattr(worker_tasks.process_session, "apply_async", _fake_apply_async)

    # Should not raise; should schedule a retry.
    worker_tasks.process_session(session_id=session_id)

    assert scheduled["kwargs"] == {"session_id": session_id}
    assert isinstance(scheduled["countdown"], float)
    assert scheduled["countdown"] >= 5.0

    with Session(engine) as db:
        row = db.scalar(select(TelegramSessions).where(TelegramSessions.id == session_row.id))
        assert row is not None
        assert row.status == "processing"

        events = list(
            db.scalars(
                select(ProcessingEvents.event)
                .where(ProcessingEvents.session_id == session_row.id)
                .order_by(ProcessingEvents.at.asc())
            )
        )
        assert "assistant_reply_attempt_failed_v0" in events
        assert "assistant_reply_retry_scheduled_v0" in events

