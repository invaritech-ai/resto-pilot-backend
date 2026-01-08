"""
Telegram webhook endpoint.

All updates are immediately enqueued to Celery for processing.
No batching - instant processing with intent classification.
"""

import logging
import secrets
from typing import cast

from fastapi import APIRouter, Request, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.workers.celery_types import CeleryDelayable
from app.workers.tasks import handle_telegram_update

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    """
    Receive Telegram webhook updates and enqueue for processing.

    All messages are processed instantly via Celery task.
    The handler uses intent classification to determine the appropriate response.
    """
    # Validate configuration
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

    # Validate webhook secret
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

    # Parse update for logging
    update = await request.json()
    update_id: int | None = None
    chat_id: int | None = None

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
        "telegram_webhook_received update_id=%s chat_id=%s",
        update_id,
        chat_id,
    )

    # Enqueue for instant processing
    try:
        async_result = cast(CeleryDelayable, handle_telegram_update).delay(update)
        logger.info(
            "telegram_webhook_enqueued task_id=%s update_id=%s chat_id=%s",
            getattr(async_result, "id", None),
            update_id,
            chat_id,
        )
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
