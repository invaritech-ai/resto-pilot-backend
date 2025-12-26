from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Mapping, cast
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.workers.celery_types import CeleryApplyAsync

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedTelegramMessage:
    update_id: int
    message_id: int
    chat_id: int
    telegram_id: int
    received_at: dt.datetime
    text: str | None = None
    caption: str | None = None
    file_kind: str | None = None
    file_id: str | None = None
    file_unique_id: str | None = None
    mime: str | None = None
    filename: str | None = None
    size: int | None = None


_HINT_COMMANDS: set[str] = {
    "/invoice",
    "/inventory",
    "/prices",
    "/recipe",
    "/menu",
    "/done",
    "/reset",
    "/help",
}


def _coerce_utc(value: dt.datetime) -> dt.datetime:
    """
    Normalize datetimes to UTC-aware.

    SQLite may return naive datetimes even when columns are declared with
    timezone=True; treat naive values as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _parse_unix_seconds(value: object) -> dt.datetime:
    if isinstance(value, int):
        return dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return dt.datetime.now(dt.UTC)


def _extract_hint_command(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    token = text.strip().split(maxsplit=1)[0].lower()
    if token in _HINT_COMMANDS:
        return token
    return None


def _pick_photo_variant(photo: object) -> dict | None:
    if not isinstance(photo, list) or not photo:
        return None

    best: dict | None = None
    best_size = -1
    for item in photo:
        if not isinstance(item, dict):
            continue
        size = item.get("file_size")
        if isinstance(size, int) and size > best_size:
            best = item
            best_size = size
    if best is not None:
        return best

    # Fallback when file_size isn't present: pick the largest by area.
    best_area = -1
    for item in photo:
        if not isinstance(item, dict):
            continue
        width = item.get("width")
        height = item.get("height")
        if isinstance(width, int) and isinstance(height, int):
            area = width * height
            if area > best_area:
                best = item
                best_area = area
    return best


def parse_update(update: Mapping[str, Any]) -> ParsedTelegramMessage | None:
    message_obj = update.get("message") or update.get("edited_message")
    if not isinstance(message_obj, dict):
        return None
    message = cast(dict[str, Any], message_obj)

    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None

    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        return None

    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None

    from_user = message.get("from")
    if not isinstance(from_user, dict):
        return None
    telegram_id = from_user.get("id")
    if not isinstance(telegram_id, int):
        return None

    received_at = _parse_unix_seconds(message.get("date"))
    text = message.get("text") if isinstance(message.get("text"), str) else None
    caption = (
        message.get("caption") if isinstance(message.get("caption"), str) else None
    )

    file_kind: str | None = None
    file_id: str | None = None
    file_unique_id: str | None = None
    mime: str | None = None
    filename: str | None = None
    size: int | None = None

    if "document" in message and isinstance(message["document"], dict):
        doc = message["document"]
        file_kind = "document"
        file_id = doc.get("file_id") if isinstance(doc.get("file_id"), str) else None
        file_unique_id = (
            doc.get("file_unique_id")
            if isinstance(doc.get("file_unique_id"), str)
            else None
        )
        filename = (
            doc.get("file_name") if isinstance(doc.get("file_name"), str) else None
        )
        mime = doc.get("mime_type") if isinstance(doc.get("mime_type"), str) else None
        size = doc.get("file_size") if isinstance(doc.get("file_size"), int) else None
    elif "photo" in message:
        best = _pick_photo_variant(message.get("photo"))
        if best is not None:
            file_kind = "photo"
            file_id = (
                best.get("file_id") if isinstance(best.get("file_id"), str) else None
            )
            file_unique_id = (
                best.get("file_unique_id")
                if isinstance(best.get("file_unique_id"), str)
                else None
            )
            size = (
                best.get("file_size")
                if isinstance(best.get("file_size"), int)
                else None
            )

    return ParsedTelegramMessage(
        update_id=update_id,
        message_id=message_id,
        chat_id=chat_id,
        telegram_id=telegram_id,
        received_at=received_at,
        text=text,
        caption=caption,
        file_kind=file_kind,
        file_id=file_id,
        file_unique_id=file_unique_id,
        mime=mime,
        filename=filename,
        size=size,
    )


def _compute_flush_at(
    *, started_at: dt.datetime, last_activity_at: dt.datetime, settings: Settings
) -> dt.datetime:
    idle_deadline = last_activity_at + dt.timedelta(
        seconds=settings.telegram_batch_idle_seconds
    )
    cap_deadline = started_at + dt.timedelta(
        seconds=settings.telegram_batch_max_seconds
    )
    return min(idle_deadline, cap_deadline)


def ingest_update(
    *,
    update: Mapping[str, Any],
    session: Session,
    settings: Settings,
    schedule_flush: bool = True,
) -> uuid.UUID | None:
    parsed = parse_update(update)
    if parsed is None:
        return None

    user = session.scalar(select(User).where(User.telegram_id == parsed.telegram_id))
    if user is None:
        logger.info(
            "telegram_ingest_user_not_registered",
            extra={"telegram_id": parsed.telegram_id, "chat_id": parsed.chat_id},
        )
        return None

    now = dt.datetime.now(dt.UTC)
    hint = _extract_hint_command(parsed.text)

    open_session = session.scalar(
        select(TelegramSessions)
        .where(
            TelegramSessions.chat_id == parsed.chat_id,
            TelegramSessions.status == "open",
        )
        .order_by(TelegramSessions.started_at.desc())
        .limit(1)
    )

    should_start_new = True
    if open_session is not None:
        idle_gap = (now - _coerce_utc(open_session.last_activity_at)).total_seconds()
        age = (now - _coerce_utc(open_session.started_at)).total_seconds()
        if (
            idle_gap < settings.telegram_batch_idle_seconds
            and age < settings.telegram_batch_max_seconds
        ):
            should_start_new = False

    if open_session is None or should_start_new:
        open_session = TelegramSessions(
            chat_id=parsed.chat_id,
            started_at=now,
            last_activity_at=now,
            flush_at=_compute_flush_at(
                started_at=now, last_activity_at=now, settings=settings
            ),
            status="open",
            hint_command=None,
        )
        session.add(open_session)
        session.flush()

    assert open_session is not None

    if hint == "/reset":
        open_session.hint_command = None
    elif hint and hint not in {"/done", "/help"}:
        open_session.hint_command = hint

    open_session.last_activity_at = now
    open_session.flush_at = _compute_flush_at(
        started_at=_coerce_utc(open_session.started_at),
        last_activity_at=now,
        settings=settings,
    )
    if hint == "/done":
        open_session.flush_at = now

    msg = TelegramMessages(
        session_id=open_session.id,
        chat_id=parsed.chat_id,
        user_id=user.id,
        telegram_id=parsed.telegram_id,
        message_id=parsed.message_id,
        update_id=parsed.update_id,
        received_at=parsed.received_at,
        text=parsed.text,
        caption=parsed.caption,
        file_id=parsed.file_id,
        file_unique_id=parsed.file_unique_id,
        file_kind=parsed.file_kind,
        mime=parsed.mime,
        filename=parsed.filename,
        size=parsed.size,
    )
    session.add(msg)

    session.add(
        ProcessingEvents(
            session_id=open_session.id,
            at=now,
            event="ingested_update",
            payload_json=None,
            error=None,
        )
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        logger.info(
            "telegram_ingest_duplicate_update",
            extra={"update_id": parsed.update_id, "chat_id": parsed.chat_id},
        )
        return None

    if not schedule_flush:
        return open_session.id

    countdown = max(0.0, (open_session.flush_at - now).total_seconds())

    if not settings.celery_broker_url:
        logger.warning("celery_broker_not_configured")
        return open_session.id

    from app.workers.tasks import flush_session  # imported lazily

    cast(CeleryApplyAsync, flush_session).apply_async(
        kwargs={
            "session_id": str(open_session.id),
            "expected_last_activity_at": open_session.last_activity_at.isoformat(),
        },
        countdown=countdown,
    )

    return open_session.id


def seal_open_session(*, chat_id: int, session: Session) -> uuid.UUID | None:
    now = dt.datetime.now(dt.UTC)

    latest_open_session_id = (
        select(TelegramSessions.id)
        .where(TelegramSessions.chat_id == chat_id, TelegramSessions.status == "open")
        .order_by(TelegramSessions.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )

    sealed_id = session.execute(
        update(TelegramSessions)
        .where(
            TelegramSessions.id == latest_open_session_id,
            TelegramSessions.status == "open",
        )
        .values(status="processing", closed_at=now)
        .returning(TelegramSessions.id)
    ).scalar_one_or_none()

    if sealed_id is None:
        return None

    session.add(
        ProcessingEvents(
            session_id=sealed_id,
            at=now,
            event="session_sealed_by_command",
            payload_json=None,
            error=None,
        )
    )

    session.commit()
    return sealed_id
