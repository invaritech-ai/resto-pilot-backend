from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings


@dataclass(frozen=True)
class TelegramResponse:
    method: str
    chat_id: int
    text: str

    def as_webhook_response(self) -> dict:
        return {"method": self.method, "chat_id": self.chat_id, "text": self.text}


def _is_superuser(*, telegram_id: int, settings: Settings) -> bool:
    return telegram_id in set(settings.telegram_superuser_ids)


def _send_message(*, chat_id: int, text: str) -> TelegramResponse:
    return TelegramResponse(method="sendMessage", chat_id=chat_id, text=text)

def handle_start(update: dict):
    pass


def process_update(*, update: dict, session: Session, settings: Settings) -> dict | None:
    del session, settings

    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if text == "/start":
        handle_start(update=update)

    if not isinstance(chat_id, int) or not isinstance(text, str) or not text.strip():
        return None

    return _send_message(chat_id=chat_id, text=text).as_webhook_response()
