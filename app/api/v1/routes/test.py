"""
Test API endpoint for testing via Celery with console output.

Purpose:
- Test the full flow exactly like production (via Celery)
- Console mode flag controls output destination (console vs Telegram)
- Use real test/dev database

Security:
- Protected by same side-channel secret token
- Do not expose publicly
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.models.user import User
from app.workers.celery_types import CeleryDelayable
from app.workers.tasks import handle_telegram_update

router = APIRouter()
logger = logging.getLogger(__name__)


class TestMessageRequest(BaseModel):
    """Request to test a message via Celery."""

    telegram_id: int = Field(
        ...,
        description="Telegram user ID (maps to users.telegram_id).",
    )
    message: str = Field(..., description="User message text to process.")
    console_mode: bool = Field(
        default=True,
        description="If true, print bot responses to console instead of sending to Telegram.",
    )


class TestMessageResponse(BaseModel):
    """Response from test message enqueuing."""

    status: str
    task_id: str | None
    chat_id: int
    telegram_id: int
    console_mode: bool


def _require_test_secret(
    *,
    settings: Settings,
    x_side_channel_secret_token: str | None,
) -> None:
    """Require side-channel secret token."""
    if not settings.side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Test endpoint not configured (SIDE_CHANNEL_SECRET_TOKEN missing)",
        )
    if not x_side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing side-channel secret token",
        )
    if not secrets.compare_digest(
        x_side_channel_secret_token, settings.side_channel_secret_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid side-channel secret token",
        )


def _make_update(*, chat_id: int, telegram_id: int, text: str) -> dict[str, Any]:
    """Create Telegram update dict."""
    now = dt.datetime.now(dt.UTC)
    ts = int(now.timestamp())
    return {
        "update_id": ts,
        "message": {
            "message_id": ts,
            "date": ts,
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "Test"},
            "text": text,
        },
    }


@router.post("/test/message")
def test_message(
    payload: TestMessageRequest,
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
    x_side_channel_secret_token: str | None = Header(
        default=None, alias="X-Side-Channel-Secret-Token"
    ),
) -> dict[str, Any]:
    """
    Test message via Celery with console output.

    Enqueues to Celery exactly like production.
    console_mode flag controls output destination.
    """
    _require_test_secret(
        settings=settings,
        x_side_channel_secret_token=x_side_channel_secret_token,
    )

    if not settings.celery_broker_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery broker not configured",
        )

    user = db.scalar(select(User).where(User.telegram_id == payload.telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {payload.telegram_id} not found. Register via /start first.",
        )

    # chat_id=0 for console mode (prints to console), else use real chat_id
    chat_id = 0 if payload.console_mode else user.chat_id

    update = _make_update(
        chat_id=chat_id,
        telegram_id=payload.telegram_id,
        text=payload.message,
    )

    try:
        async_result = cast(CeleryDelayable, handle_telegram_update).delay(update)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to enqueue: {type(exc).__name__}",
        ) from exc

    return {
        "status": "enqueued",
        "task_id": getattr(async_result, "id", None),
        "chat_id": chat_id,
        "telegram_id": payload.telegram_id,
        "console_mode": payload.console_mode,
        "note": "Check worker logs for output" if payload.console_mode else "Response sent to Telegram",
    }
