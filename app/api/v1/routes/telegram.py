import logging
import secrets

from fastapi import APIRouter, Request
from fastapi import Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.deps import get_settings_dep
from app.core.config import Settings
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram webhook secret",
        )
    if not secrets.compare_digest(
        x_telegram_bot_api_secret_token, settings.telegram_webhook_secret_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )

    update = await request.json()
    logger.info("telegram_webhook_received", extra={"update": update})

    handle_telegram_update.delay(update)
    return JSONResponse({"status": "ok"})
