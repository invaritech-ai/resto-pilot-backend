from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

from sqlalchemy import select

from app.ai.openai_client import OpenAIError
from app.ai.session_reply import generate_session_reply
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.telegram.bot_api import send_message
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def process_session(*, session_id: str, task_id: str | None = None) -> None:
    session_uuid = _parse_uuid(session_id)

    with worker_db_session() as db:
        db_session = db.execute(
            select(TelegramSessions)
            .where(TelegramSessions.id == session_uuid)
            .with_for_update()
        ).scalar_one_or_none()
        if db_session is None:
            logger.info(
                "process_session_noop_session_not_found task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return
        if db_session.status == "closed":
            logger.info(
                "process_session_noop_already_closed task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            return

        sent_event = db.execute(
            select(ProcessingEvents)
            .where(
                ProcessingEvents.session_id == session_uuid,
                ProcessingEvents.event == "assistant_reply_sent_v0",
            )
            .order_by(ProcessingEvents.at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if sent_event is not None:
            logger.info(
                "process_session_noop_reply_already_sent task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            if db_session.status != "closed":
                now = dt.datetime.now(dt.UTC)
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
            return

        messages = list(
            db.scalars(
                select(TelegramMessages)
                .where(TelegramMessages.session_id == session_uuid)
                .order_by(
                    TelegramMessages.received_at.asc(), TelegramMessages.message_id.asc()
                )
            )
        )

        hint = db_session.hint_command
        file_kinds = [m.file_kind for m in messages if m.file_kind]
        mime_types = [m.mime for m in messages if m.mime]

        logger.info(
            "process_session_loaded task_id=%s session_id=%s status=%s message_count=%s hint=%s",
            task_id,
            session_id,
            db_session.status,
            len(messages),
            hint,
        )

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
        db.commit()

        generated_event = db.execute(
            select(ProcessingEvents)
            .where(
                ProcessingEvents.session_id == session_uuid,
                ProcessingEvents.event == "assistant_reply_generated_v0",
            )
            .order_by(ProcessingEvents.at.desc())
            .limit(1)
        ).scalar_one_or_none()

        reply_text: str | None = None
        if generated_event is not None and isinstance(generated_event.payload_json, str):
            try:
                payload = json.loads(generated_event.payload_json)
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    reply_text = payload["text"]
            except json.JSONDecodeError:
                reply_text = None

        settings = get_settings()
        if reply_text is None:
            try:
                reply = generate_session_reply(
                    messages=messages, hint_command=hint, settings=settings
                )
            except OpenAIError as exc:
                db.add(
                    ProcessingEvents(
                        session_id=session_uuid,
                        at=dt.datetime.now(dt.UTC),
                        event="assistant_reply_attempt_failed_v0",
                        payload_json=None,
                        error=str(exc),
                    )
                )
                db.commit()
                raise

            reply_text = reply.text
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_generated_v0",
                    payload_json=json.dumps(
                        {"text": reply.text, "model": reply.model}, ensure_ascii=False
                    ),
                    error=None,
                )
            )
            db.commit()

        try:
            send_message(chat_id=db_session.chat_id, text=reply_text, settings=settings)
        except Exception as exc:
            db.add(
                ProcessingEvents(
                    session_id=session_uuid,
                    at=dt.datetime.now(dt.UTC),
                    event="assistant_reply_send_failed_v0",
                    payload_json=None,
                    error=repr(exc),
                )
            )
            db.commit()
            raise

        final_now = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=final_now,
                event="assistant_reply_sent_v0",
                payload_json=None,
                error=None,
            )
        )

        db_session.status = "closed"
        if db_session.closed_at is None:
            db_session.closed_at = final_now
        db.add(
            ProcessingEvents(
                session_id=session_uuid,
                at=final_now,
                event="session_processed_v0",
                payload_json=None,
                error=None,
            )
        )
        db.commit()
