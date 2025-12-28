import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.telegram.handler import handle_update


def _make_start_update(*, update_id: int, message_id: int, chat_id: int, telegram_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()),
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "A"},
            "text": "/start",
        },
    }


def test_start_bypasses_batching_and_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    sent: dict[str, object] = {}

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
        sent["chat_id"] = chat_id
        sent["text"] = text

    monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

    with Session(engine) as db:
        handle_update(
            update=_make_start_update(update_id=1, message_id=1, chat_id=10, telegram_id=10),
            db=db,
            settings=settings,
        )

        assert sent["chat_id"] == 10
        assert isinstance(sent["text"], str)

        session_row = db.scalar(select(TelegramSessions).where(TelegramSessions.chat_id == 10))
        assert session_row is not None

        messages = list(
            db.scalars(select(TelegramMessages).where(TelegramMessages.session_id == session_row.id))
        )
        assert len(messages) == 1
