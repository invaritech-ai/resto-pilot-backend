from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate


@dataclass(frozen=True)
class TelegramResponse:
    method: str
    chat_id: int
    text: str

    def as_webhook_response(self) -> dict:
        return {"method": self.method, "chat_id": self.chat_id, "text": self.text}


def _send_message(*, chat_id: int, text: str) -> TelegramResponse:
    return TelegramResponse(method="sendMessage", chat_id=chat_id, text=text)


logger = logging.getLogger(__name__)


def _first_name_from_full_name(full_name: str | None) -> str | None:
    if not isinstance(full_name, str):
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


def process_update(
    *, update: dict, session: Session, settings: Settings
) -> dict | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    user_info = message.get("from") or {}
    telegram_id = user_info.get("id")

    if not isinstance(chat_id, int) or not isinstance(text, str) or not text.strip():
        return None

    if isinstance(telegram_id, int) and text.strip().startswith("/start"):
        payload = TelegramUserCreate(
            telegram_id=telegram_id,
            chat_id=chat_id,
            first_name=user_info.get("first_name"),
            last_name=user_info.get("last_name"),
            username=user_info.get("username"),
        )
        user = UserService(session).get_or_create(payload)
        first_name = _first_name_from_full_name(user.full_name)
        greeting = f"Hi {first_name}! " if first_name else "Hi! "
        return _send_message(
            chat_id=chat_id,
            text=greeting + "You're registered. Send me a message to get started.",
        ).as_webhook_response()

    if text.strip() == "/start":
        return _send_message(
            chat_id=chat_id,
            text="Welcome! Please message me from a Telegram account.",
        ).as_webhook_response()

    return None
