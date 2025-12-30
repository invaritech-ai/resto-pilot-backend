import datetime as dt
import logging
import secrets
from typing import cast

from fastapi import APIRouter, Request
from fastapi import Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.commands import extract_command
from app.telegram.ingest import ingest_update, parse_update
from app.telegram.session_lock import lock_chat_id
from app.workers.celery_types import CeleryDelayable
from app.workers.tasks import handle_telegram_update

router = APIRouter()
logger = logging.getLogger(__name__)

FORCE_FLUSH_COMMANDS: frozenset[str] = frozenset({"/respond", "/done"})


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db_dep),
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    update_id: int | None = None
    chat_id: int | None = None
    if not settings.telegram_webhook_secret_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook is not configured",
        )
    if not settings.celery_broker_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message broker is not configured",
        )
    if not x_telegram_bot_api_secret_token:
        logger.warning("telegram_webhook_missing_secret_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram webhook secret",
        )
    if not secrets.compare_digest(
        x_telegram_bot_api_secret_token, settings.telegram_webhook_secret_token
    ):
        logger.warning("telegram_webhook_invalid_secret_header")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )

    update = await request.json()
    if isinstance(update, dict):
        update_id_raw = update.get("update_id")
        if isinstance(update_id_raw, int):
            update_id = update_id_raw
        message = update.get("message") or update.get("edited_message") or {}
        if isinstance(message, dict):
            chat_id_raw = (message.get("chat") or {}).get("id")
            if isinstance(chat_id_raw, int):
                chat_id = chat_id_raw

    logger.info(
        "telegram_webhook_received update_id=%s chat_id=%s broker_configured=%s",
        update_id,
        chat_id,
        bool(settings.celery_broker_url),
    )

    try:
        if not settings.telegram_batching_enabled:
            async_result = handle_telegram_update.delay(update)
            logger.info(
                "telegram_webhook_enqueued task_id=%s update_id=%s chat_id=%s",
                getattr(async_result, "id", None),
                update_id,
                chat_id,
            )
            return JSONResponse({"status": "ok"})

        command: str | None = None
        message = update.get("message") or update.get("edited_message")
        if isinstance(message, dict):
            text = message.get("text") if isinstance(message.get("text"), str) else None
            caption = (
                message.get("caption") if isinstance(message.get("caption"), str) else None
            )
            command, _args = extract_command(text, caption)

        if command == "/start":
            async_result = handle_telegram_update.delay(update)
            logger.info(
                "telegram_webhook_enqueued task_id=%s update_id=%s chat_id=%s",
                getattr(async_result, "id", None),
                update_id,
                chat_id,
            )
            return JSONResponse({"status": "ok"})

        if command in FORCE_FLUSH_COMMANDS:
            parsed = parse_update(update)
            if parsed is None:
                return JSONResponse({"status": "ok"})

            if db.scalar(
                select(TelegramMessages.id).where(TelegramMessages.update_id == parsed.update_id)
            ) is not None:
                return JSONResponse({"status": "ok"})

            lock_chat_id(session=db, chat_id=parsed.chat_id)

            user = db.scalar(select(User).where(User.telegram_id == parsed.telegram_id))
            if user is None:
                return JSONResponse({"status": "ok"})

            open_session = db.execute(
                select(TelegramSessions)
                .where(
                    TelegramSessions.chat_id == parsed.chat_id,
                    TelegramSessions.status == "open",
                )
                .order_by(TelegramSessions.started_at.desc())
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()

            if open_session is None:
                return JSONResponse({"status": "ok"})

            now = dt.datetime.now(dt.UTC)
            db.add(
                TelegramMessages(
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
            )

            open_session.last_activity_at = now
            open_session.flush_at = now
            open_session.status = "processing"
            open_session.closed_at = now

            db.add(
                ProcessingEvents(
                    session_id=open_session.id,
                    at=now,
                    event="session_sealed_by_command",
                    payload_json=None,
                    error=None,
                )
            )

            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return JSONResponse({"status": "ok"})

            from app.workers.tasks import process_session, send_session_ack  # imported lazily

            cast(CeleryDelayable, send_session_ack).delay(session_id=str(open_session.id))
            cast(CeleryDelayable, process_session).delay(session_id=str(open_session.id))
            return JSONResponse({"status": "ok"})

        ingest_update(update=update, session=db, settings=settings)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "telegram_webhook_enqueue_failed update_id=%s chat_id=%s error=%r",
            update_id,
            chat_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue Telegram update",
        ) from exc

    return JSONResponse({"status": "ok"})
