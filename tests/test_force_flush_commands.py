import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate
from app.telegram.handler import handle_update
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


@pytest.mark.parametrize("command", ["/done", "/respond"])
def test_force_flush_command_seals_session_and_enqueues_processing(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    with Session(engine) as db:
        UserService(db).get_or_create(
            TelegramUserCreate(
                telegram_id=1,
                chat_id=10,
                first_name="A",
                last_name=None,
                username=None,
            )
        )

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
            schedule_flush=False,
        )
        assert session_id is not None

        enqueued: dict[str, object] = {}

        from app.workers import tasks as worker_tasks

        def _fake_delay(*, session_id: str):
            enqueued["session_id"] = session_id
            return object()

        monkeypatch.setattr(worker_tasks.process_session, "delay", _fake_delay)

        def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
            enqueued["reply_chat_id"] = chat_id
            enqueued["reply_text"] = text

        monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

        handle_update(
            update=_make_update(
                update_id=2,
                message_id=2,
                chat_id=10,
                telegram_id=1,
                text=command,
            ),
            db=db,
            settings=settings,
        )

        sealed = db.scalar(select(TelegramSessions).where(TelegramSessions.id == session_id))
        assert sealed is not None
        assert sealed.status == "processing"
        assert sealed.closed_at is not None

        assert enqueued["session_id"] == str(session_id)
        assert enqueued["reply_chat_id"] == 10
        assert isinstance(enqueued["reply_text"], str)

        messages = list(
            db.scalars(select(TelegramMessages).where(TelegramMessages.session_id == session_id))
        )
        assert len(messages) == 2

        new_session_id = ingest_update(
            update=_make_update(
                update_id=3,
                message_id=3,
                chat_id=10,
                telegram_id=1,
                text="after",
            ),
            session=db,
            settings=settings,
            schedule_flush=False,
        )
        assert new_session_id is not None
        assert new_session_id != session_id
        newer = db.scalar(select(TelegramSessions).where(TelegramSessions.id == new_session_id))
        assert newer is not None
        assert newer.status == "open"


@pytest.mark.parametrize("command", ["/done", "/respond"])
def test_force_flush_command_with_no_open_session_sends_nothing_to_process(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    with Session(engine) as db:
        UserService(db).get_or_create(
            TelegramUserCreate(
                telegram_id=1,
                chat_id=10,
                first_name="A",
                last_name=None,
                username=None,
            )
        )

        enqueued: dict[str, object] = {}

        from app.workers import tasks as worker_tasks

        def _fake_delay(*, session_id: str):
            enqueued["process_session_id"] = session_id
            return object()

        monkeypatch.setattr(worker_tasks.process_session, "delay", _fake_delay)

        def _fake_send_message(*, chat_id: int, text: str, settings: Settings) -> None:
            enqueued["reply_chat_id"] = chat_id
            enqueued["reply_text"] = text

        monkeypatch.setattr("app.telegram.handler.send_message", _fake_send_message)

        handle_update(
            update=_make_update(
                update_id=1,
                message_id=1,
                chat_id=10,
                telegram_id=1,
                text=command,
            ),
            db=db,
            settings=settings,
        )

        assert "process_session_id" not in enqueued
        assert enqueued["reply_chat_id"] == 10
        assert isinstance(enqueued["reply_text"], str)

        assert (
            db.scalar(select(TelegramSessions).where(TelegramSessions.chat_id == 10)) is None
        )


def test_non_instant_message_is_ingested_in_batching_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    with Session(engine) as db:
        UserService(db).get_or_create(
            TelegramUserCreate(
                telegram_id=1,
                chat_id=10,
                first_name="A",
                last_name=None,
                username=None,
            )
        )

        from app.workers import tasks as worker_tasks

        def _fake_apply_async(*args, **kwargs):
            return object()

        monkeypatch.setattr(worker_tasks.flush_session, "apply_async", _fake_apply_async)
        monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", _fake_apply_async)

        def _fail_send_message(*args, **kwargs):
            raise AssertionError("send_message should not be called in batching mode")

        monkeypatch.setattr("app.telegram.handler.send_message", _fail_send_message)

        handle_update(
            update=_make_update(
                update_id=1,
                message_id=1,
                chat_id=10,
                telegram_id=1,
                text="hello",
            ),
            db=db,
            settings=settings,
        )

        session_row = db.scalar(select(TelegramSessions).where(TelegramSessions.chat_id == 10))
        assert session_row is not None
        assert session_row.status == "open"

        messages = list(
            db.scalars(select(TelegramMessages).where(TelegramMessages.session_id == session_row.id))
        )
        assert len(messages) == 1


def test_caption_command_sets_session_hint_in_batching_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = Settings(
        telegram_batching_enabled=True,
        celery_broker_url="redis://localhost",
        telegram_bot_token="test-token",
    )

    with Session(engine) as db:
        UserService(db).get_or_create(
            TelegramUserCreate(
                telegram_id=1,
                chat_id=10,
                first_name="A",
                last_name=None,
                username=None,
            )
        )

        from app.workers import tasks as worker_tasks

        def _fake_apply_async(*args, **kwargs):
            return object()

        monkeypatch.setattr(worker_tasks.flush_session, "apply_async", _fake_apply_async)
        monkeypatch.setattr(worker_tasks.send_message_backchannel, "apply_async", _fake_apply_async)

        def _fail_send_message(*args, **kwargs):
            raise AssertionError("send_message should not be called in batching mode")

        monkeypatch.setattr("app.telegram.handler.send_message", _fail_send_message)

        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()),
                "chat": {"id": 10},
                "from": {"id": 1, "first_name": "A"},
                "caption": "/inventory",
                "document": {"file_id": "x", "file_unique_id": "ux", "file_name": "a.txt"},
            },
        }

        handle_update(update=update, db=db, settings=settings)

        session_row = db.scalar(select(TelegramSessions).where(TelegramSessions.chat_id == 10))
        assert session_row is not None
        assert session_row.hint_command == "/inventory"
