from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import cast

from sqlalchemy import select

from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.workers.celery_app import celery_app
from app.workers.celery_types import CeleryDelayable
from app.workers.db import worker_db_session


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


@celery_app.task(name="flush_session")
def flush_session(*, session_id: str, expected_last_activity_at: str) -> None:
    expected = dt.datetime.fromisoformat(expected_last_activity_at)
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None:
            return
        if db_session.status != "open":
            return
        if db_session.last_activity_at != expected:
            return

        now = dt.datetime.now(dt.UTC)
        db_session.status = "processing"
        db_session.closed_at = now
        db.add(
            ProcessingEvents(
                session_id=db_session.id,
                at=now,
                event="session_flushed",
                payload_json=None,
                error=None,
            )
        )
        db.commit()

    cast(CeleryDelayable, process_session).delay(session_id=session_id)


@celery_app.task(name="process_session")
def process_session(*, session_id: str) -> None:
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
        db_session = db.scalar(
            select(TelegramSessions).where(TelegramSessions.id == session_uuid)
        )
        if db_session is None:
            return

        messages = list(
            db.scalars(
                select(TelegramMessages)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(TelegramMessages.received_at.asc())
            )
        )

        hint = db_session.hint_command
        file_kinds = [m.file_kind for m in messages if m.file_kind]
        mime_types = [m.mime for m in messages if m.mime]

        routing_plan = {
            "hint_command": hint,
            "message_count": len(messages),
            "file_kinds": file_kinds,
            "mime_types": mime_types,
        }

        now = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=now,
                event="router_plan_v0",
                payload_json=json.dumps(routing_plan),
                error=None,
            )
        )

        db_session.status = "closed"
        if db_session.closed_at is None:
            db_session.closed_at = now

        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=now,
                event="session_processed_v0",
                payload_json=None,
                error=None,
            )
        )
        db.commit()


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict):
    pass
