import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.handler import handle_update


def _make_update(*, update_id: int, message_id: int, chat_id: int, telegram_id: int, text: str) -> dict:
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


def test_phone_intake_bypasses_batching(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    sent: list[tuple[int, str]] = []

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

    with Session(engine) as db:
        handle_update(
            update=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=10, text="/start"),
            db=db,
            settings=settings,
        )

        user = db.scalar(select(User).where(User.telegram_id == 10))
        assert user is not None
        assert user.state == "COLLECT_PHONE"
        assert user.phone is None
        assert sent and sent[-1][0] == 10

        open_sessions_before = list(
            db.scalars(
                select(TelegramSessions).where(
                    TelegramSessions.chat_id == 10, TelegramSessions.status == "open"
                )
            )
        )
        assert len(open_sessions_before) == 1

        handle_update(
            update=_make_update(
                update_id=2, message_id=2, chat_id=10, telegram_id=10, text="+14155550101"
            ),
            db=db,
            settings=settings,
        )

        user2 = db.scalar(select(User).where(User.telegram_id == 10))
        assert user2 is not None
        assert user2.state == "IDLE"
        assert user2.phone == "+14155550101"

        open_sessions_after = list(
            db.scalars(
                select(TelegramSessions).where(
                    TelegramSessions.chat_id == 10, TelegramSessions.status == "open"
                )
            )
        )
        assert len(open_sessions_after) == 1

        closed_sessions = list(
            db.scalars(
                select(TelegramSessions).where(
                    TelegramSessions.chat_id == 10, TelegramSessions.status == "closed"
                )
            )
        )
        assert len(closed_sessions) == 1


def test_phone_intake_reprompts_on_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    sent: list[str] = []

    def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

    with Session(engine) as db:
        handle_update(
            update=_make_update(update_id=1, message_id=1, chat_id=10, telegram_id=10, text="/start"),
            db=db,
            settings=settings,
        )

        handle_update(
            update=_make_update(update_id=2, message_id=2, chat_id=10, telegram_id=10, text="hello"),
            db=db,
            settings=settings,
        )

        user = db.scalar(select(User).where(User.telegram_id == 10))
        assert user is not None
        assert user.state == "COLLECT_PHONE"
        assert user.phone is None
        assert any("phone number" in text.lower() for text in sent)

