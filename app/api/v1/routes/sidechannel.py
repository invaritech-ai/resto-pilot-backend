"""
Side-channel endpoint for local/staging testing.

Purpose:
- Exercise the same update handling pipeline as Telegram (API enqueues; worker processes).
- Enqueue Planner-only runs for prompt tuning without blocking the API.

Security:
- Protected by a shared secret header. Do not expose publicly.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.conversation.context import load_context
from app.core.config import Settings
from app.db.models.user import User
from app.workers.celery_types import CeleryDelayable
from app.workers.tasks import handle_telegram_update, sidechannel_planner_only

router = APIRouter()


class SideChannelMessageRequest(BaseModel):
    telegram_id: int | str = Field(
        ...,
        description="Telegram user id (maps to users.telegram_id). Use 'console-log' to print bot output to console (requires user_telegram_id).",
    )
    user_telegram_id: int | None = Field(
        None,
        description="When telegram_id='console-log', this is the real Telegram user id to attribute the request to.",
    )
    chat_id: int | None = Field(None, description="Optional chat_id override.")
    text: str = Field(..., description="Incoming message text.")
    mode: Literal["planner_only", "enqueue"] = Field(
        "enqueue",
        description="enqueue: enqueue to Celery like production; planner_only: enqueue PlannerLLM only (no tool execution).",
    )
    context_override: dict[str, Any] | None = Field(
        default=None,
        description="Optional context overrides merged into users.context for planner_only.",
    )
    recent_turns_limit: int = Field(
        20,
        ge=0,
        le=50,
        description="How many recent turns to include for planner_only.",
    )


def _require_side_channel_secret(
    *,
    settings: Settings,
    x_side_channel_secret_token: str | None,
) -> None:
    if not settings.side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Side-channel is not configured",
        )
    if not x_side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing side-channel secret",
        )
    if not secrets.compare_digest(
        x_side_channel_secret_token, settings.side_channel_secret_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid side-channel secret",
        )


def _make_update(*, chat_id: int, telegram_id: int, text: str) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    ts = int(now.timestamp())
    update_id = ts
    message_id = ts
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": ts,
            "chat": {"id": chat_id},
            "from": {"id": telegram_id, "first_name": "SideChannel"},
            "text": text,
        },
    }


def _load_recent_turns(*, db: Session, chat_id: int, limit: int) -> list[dict[str, str]]:
    if limit <= 0:
        return []

    incoming = db.scalars(
        select(TelegramMessages)
        .where(TelegramMessages.chat_id == chat_id)
        .order_by(TelegramMessages.received_at.desc())
        .limit(limit)
    ).all()

    outgoing = db.scalars(
        select(TelegramOutgoingMessages)
        .where(
            TelegramOutgoingMessages.chat_id == chat_id,
            TelegramOutgoingMessages.kind.in_(("reply", "start_reply")),
        )
        .order_by(TelegramOutgoingMessages.sent_at.desc())
        .limit(limit)
    ).all()

    items: list[tuple[dt.datetime, str, str]] = []
    for msg in incoming:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
        content = "\n".join(parts).strip()
        if content:
            items.append((msg.received_at, "user", content))
    for msg in outgoing:
        content = msg.text.strip() if isinstance(msg.text, str) else ""
        if content:
            items.append((msg.sent_at, "assistant", content))

    items.sort(key=lambda row: row[0])
    return [{"role": role, "content": content} for _, role, content in items][-limit:]


@router.post("/sidechannel/message")
def sidechannel_message(
    payload: SideChannelMessageRequest,
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
    x_side_channel_secret_token: str | None = Header(
        default=None, alias="X-Side-Channel-Secret-Token"
    ),
) -> dict[str, Any]:
    _require_side_channel_secret(
        settings=settings,
        x_side_channel_secret_token=x_side_channel_secret_token,
    )

    text = (payload.text or "").strip()

    console_mode = isinstance(payload.telegram_id, str) and payload.telegram_id.strip().lower() == "console-log"
    telegram_id: int
    if console_mode:
        if not isinstance(payload.user_telegram_id, int):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_telegram_id is required when telegram_id='console-log'.",
            )
        telegram_id = payload.user_telegram_id
    else:
        if not isinstance(payload.telegram_id, int):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="telegram_id must be an integer (or 'console-log').",
            )
        telegram_id = payload.telegram_id

    user = db.scalar(select(User).where(User.telegram_id == telegram_id))

    # Allow /start simulation even if the user doesn't exist yet.
    if user is None:
        if not text.startswith("/start"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found. Send /start from Telegram first to register.",
            )
        if not isinstance(payload.chat_id, int):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="chat_id is required to simulate /start for a new user.",
            )
        # If console_mode, force chat_id=0 so bot output prints to console.
        chat_id = 0 if console_mode else payload.chat_id
    else:
        if console_mode:
            chat_id = 0
        else:
            chat_id = payload.chat_id if isinstance(payload.chat_id, int) else user.chat_id
    if not isinstance(chat_id, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_id is required (user has no chat_id).",
        )

    if payload.mode == "planner_only":
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="planner_only requires an existing user (send /start first).",
            )
        try:
            async_result = cast(CeleryDelayable, sidechannel_planner_only).delay(
                telegram_id=telegram_id,
                chat_id=chat_id,
                message_text=text,
                recent_turns_limit=int(payload.recent_turns_limit or 0),
                context_override=payload.context_override if isinstance(payload.context_override, dict) else None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to enqueue planner_only task: {type(exc).__name__}",
            ) from exc
        return {
            "status": "ok",
            "mode": "planner_only",
            "task_id": getattr(async_result, "id", None),
            "chat_id": chat_id,
            "telegram_id": telegram_id,
        }

    if payload.mode == "enqueue":
        if not settings.celery_broker_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Message broker is not configured",
            )
        update = _make_update(chat_id=chat_id, telegram_id=telegram_id, text=text)
        try:
            async_result = cast(CeleryDelayable, handle_telegram_update).delay(update)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to enqueue sidechannel update: {type(exc).__name__}",
            ) from exc
        return {
            "status": "ok",
            "mode": "enqueue",
            "task_id": getattr(async_result, "id", None),
            "chat_id": chat_id,
            "telegram_id": telegram_id,
        }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported mode. Use mode='enqueue' for production-like behavior or mode='planner_only' to enqueue planner.",
    )
