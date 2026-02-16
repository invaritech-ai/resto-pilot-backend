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
import hashlib
import logging
import os
import secrets
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.models.user import User
from app.telegram.ack_handler import send_instant_ack
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
    file_id: str | None = Field(
        default=None,
        description="Optional file_id to simulate file upload (for testing file processing).",
    )
    file_type: str | None = Field(
        default=None,
        description="Optional file type: document, photo, voice, video, or audio.",
    )
    file_path: str | None = Field(
        default=None,
        description="Optional local file path for actual file processing (bypasses Telegram API).",
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


def _make_update(
    *,
    chat_id: int,
    telegram_id: int,
    text: str,
    file_id: str | None = None,
    file_type: str | None = None,
) -> dict[str, Any]:
    """Create Telegram update dict."""
    now = dt.datetime.now(dt.UTC)
    ts = int(now.timestamp())

    message: dict[str, Any] = {
        "message_id": ts,
        "date": ts,
        "chat": {"id": chat_id},
        "from": {"id": telegram_id, "first_name": "Test"},
    }

    # Add text if provided (can be empty for file-only messages)
    if text:
        message["text"] = text

    # Add file if provided
    if file_id and file_type:
        if file_type == "document":
            message["document"] = {
                "file_id": file_id,
                "file_unique_id": f"test_{file_id[:8]}",
                "file_name": "test_file.pdf",
                "mime_type": "application/pdf",
                "file_size": 12345,
            }
        elif file_type == "photo":
            message["photo"] = [
                {
                    "file_id": file_id,
                    "file_unique_id": f"test_{file_id[:8]}",
                    "width": 1920,
                    "height": 1080,
                    "file_size": 54321,
                }
            ]
        elif file_type == "voice":
            message["voice"] = {
                "file_id": file_id,
                "file_unique_id": f"test_{file_id[:8]}",
                "duration": 30,
                "mime_type": "audio/ogg",
                "file_size": 9876,
            }
        elif file_type == "video":
            message["video"] = {
                "file_id": file_id,
                "file_unique_id": f"test_{file_id[:8]}",
                "width": 1920,
                "height": 1080,
                "duration": 60,
                "mime_type": "video/mp4",
                "file_size": 123456,
            }
        elif file_type == "audio":
            message["audio"] = {
                "file_id": file_id,
                "file_unique_id": f"test_{file_id[:8]}",
                "duration": 180,
                "mime_type": "audio/mpeg",
                "file_size": 45678,
            }

        # File messages can have caption instead of text
        if text:
            message["caption"] = message.pop("text", "")

    return {
        "update_id": ts,
        "message": message,
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

    # Handle local file if provided
    file_id = payload.file_id
    file_type = payload.file_type
    local_file_path: str | None = None

    if payload.file_path:
        # Verify file exists
        if not os.path.isfile(payload.file_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File not found: {payload.file_path}",
            )

        # Auto-detect file type from extension if not provided
        if not file_type:
            ext = os.path.splitext(payload.file_path)[1].lower()
            if ext in [".pdf", ".doc", ".docx", ".txt", ".csv", ".xlsx"]:
                file_type = "document"
            elif ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                file_type = "photo"
            elif ext in [".mp3", ".m4a", ".wav", ".flac"]:
                file_type = "audio"
            elif ext in [".mp4", ".mov", ".avi", ".mkv"]:
                file_type = "video"
            elif ext in [".ogg", ".oga"]:
                file_type = "voice"
            else:
                file_type = "document"  # Default to document

        # Generate file_id from path hash if not provided
        if not file_id:
            path_hash = hashlib.sha256(payload.file_path.encode()).hexdigest()[:16]
            file_id = f"local_{path_hash}"

        # Store absolute path for worker
        local_file_path = os.path.abspath(payload.file_path)

    # Build Telegram update first (same structure as real Telegram)
    update = _make_update(
        chat_id=chat_id,
        telegram_id=payload.telegram_id,
        text=payload.message,
        file_id=file_id,
        file_type=file_type,
    )

    # Send instant ACK and create session for telemetry (shared with telegram endpoint)
    session_id_str = send_instant_ack(
        db=db,
        settings=settings,
        update=update,
        console_mode=payload.console_mode,
    )

    # Add session_id to update so worker can reuse it
    if session_id_str:
        update["_session_id"] = session_id_str

    # Add local file path to update so worker can read it directly
    if local_file_path:
        update["_local_file_path"] = local_file_path

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
