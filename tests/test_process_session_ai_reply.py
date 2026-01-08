"""
Test the session processor with the new intent-driven approach.
"""

import datetime as dt
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.intent_classifier import Intent, ClassifiedIntent
from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.workers.tasks import process_session


def test_process_session_generates_and_sends_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that process_session classifies intent and sends a response."""
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

        db.add_all(
            [
                TelegramMessages(
                    session_id=session_row.id,
                    chat_id=10,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    message_id=1,
                    update_id=1001,
                    received_at=started,
                    text="What can you do?",
                    caption=None,
                    file_id=None,
                    file_unique_id=None,
                    file_kind=None,
                    mime=None,
                    filename=None,
                    size=None,
                ),
            ]
        )
        db.commit()

        session_id = str(session_row.id)

    @contextmanager
    def _fake_worker_db_session():
        with Session(engine) as db:
            yield db

    from app.processing import session_processor

    monkeypatch.setattr(
        session_processor,
        "worker_db_session",
        _fake_worker_db_session,
    )
    monkeypatch.setattr(
        session_processor,
        "get_settings",
        lambda: Settings(telegram_bot_token="test", openai_api_key="test"),
    )

    # Track calls
    called: dict[str, Any] = {"classify_called": 0, "sent": 0}

    def _fake_classify_intent(**kwargs):
        called["classify_called"] += 1
        return ClassifiedIntent(
            intent=Intent.SHOW_MENU,
            params={},
            confidence=1.0,
            model="test",
            latency_ms=0,
        )

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> int:
        called["sent"] += 1
        called["chat_id"] = chat_id
        called["text"] = text
        return 12345

    monkeypatch.setattr(
        session_processor,
        "classify_intent",
        _fake_classify_intent,
    )
    monkeypatch.setattr(
        session_processor,
        "send_message",
        _fake_send_message,
    )

    process_session(session_id=session_id)

    assert called["classify_called"] == 1
    assert called["sent"] == 1
    assert called["chat_id"] == 10
    assert isinstance(called["text"], str)
    # Should contain the main menu response
    assert "what i can help" in called["text"].lower() or "menu" in called["text"].lower()

    with Session(engine) as db:
        sealed = db.scalar(
            select(TelegramSessions).where(TelegramSessions.id == session_row.id)
        )
        assert sealed is not None
        assert sealed.status == "closed"

        events = list(
            db.scalars(
                select(ProcessingEvents.event)
                .where(ProcessingEvents.session_id == session_row.id)
                .order_by(ProcessingEvents.at.asc())
            )
        )
        assert "assistant_reply_sent_v0" in events
        assert "session_processed_v0" in events

    # Second run should no-op (reply already sent)
    process_session(session_id=session_id)
    assert called["classify_called"] == 1
    assert called["sent"] == 1


def test_process_session_handles_unknown_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that unknown intents get the 'can't help' response."""
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
                text="What's the weather like?",
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

    monkeypatch.setattr(
        session_processor,
        "worker_db_session",
        _fake_worker_db_session,
    )
    monkeypatch.setattr(
        session_processor,
        "get_settings",
        lambda: Settings(telegram_bot_token="test", openai_api_key="test"),
    )

    called: dict[str, Any] = {"sent": 0}

    def _fake_classify_intent(**kwargs):
        return ClassifiedIntent(
            intent=Intent.UNKNOWN,
            params={},
            confidence=0.9,
            model="test",
            latency_ms=0,
        )

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> int:
        called["sent"] += 1
        called["text"] = text
        return 12345

    monkeypatch.setattr(
        session_processor,
        "classify_intent",
        _fake_classify_intent,
    )
    monkeypatch.setattr(
        session_processor,
        "send_message",
        _fake_send_message,
    )

    process_session(session_id=session_id)

    assert called["sent"] == 1
    assert "can't help" in called["text"].lower()
