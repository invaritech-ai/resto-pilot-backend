from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.telegram.processor import process_update


def _make_settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        telegram_bot_username="MyBot",
        telegram_superuser_ids=[],
    )


def _make_update(*, chat_id: int, text: str) -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        }
    }


def test_echo_bot_replies_with_same_text():
    settings = _make_settings()
    engine = create_engine(settings.database_url, future=True)

    with Session(engine) as session:
        resp = process_update(
            update=_make_update(chat_id=10, text="hello"),
            session=session,
            settings=settings,
        )
        assert resp == {"method": "sendMessage", "chat_id": 10, "text": "hello"}


def test_echo_bot_ignores_blank_text():
    settings = _make_settings()
    engine = create_engine(settings.database_url, future=True)

    with Session(engine) as session:
        resp = process_update(
            update=_make_update(chat_id=10, text="   "),
            session=session,
            settings=settings,
        )
        assert resp is None
