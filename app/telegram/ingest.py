"""
Telegram update parsing utilities.

Provides the ParsedTelegramMessage dataclass and parse_update function
for extracting structured data from Telegram webhook payloads.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping, cast


@dataclass(frozen=True)
class ParsedTelegramMessage:
    """Structured representation of a Telegram message."""

    update_id: int
    message_id: int
    chat_id: int
    telegram_id: int
    received_at: dt.datetime
    text: str | None = None
    caption: str | None = None
    file_kind: str | None = None
    file_id: str | None = None
    file_unique_id: str | None = None
    mime: str | None = None
    filename: str | None = None
    size: int | None = None


def _parse_unix_seconds(value: object) -> dt.datetime:
    """Convert Unix timestamp to datetime."""
    if isinstance(value, int):
        return dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return dt.datetime.now(dt.UTC)


def _pick_photo_variant(photo: object) -> dict | None:
    """Pick the best quality photo variant from Telegram's array."""
    if not isinstance(photo, list) or not photo:
        return None

    best: dict | None = None
    best_size = -1
    for item in photo:
        if not isinstance(item, dict):
            continue
        size = item.get("file_size")
        if isinstance(size, int) and size > best_size:
            best = item
            best_size = size
    if best is not None:
        return best

    # Fallback when file_size isn't present: pick the largest by area.
    best_area = -1
    for item in photo:
        if not isinstance(item, dict):
            continue
        width = item.get("width")
        height = item.get("height")
        if isinstance(width, int) and isinstance(height, int):
            area = width * height
            if area > best_area:
                best = item
                best_area = area
    return best


def parse_update(update: Mapping[str, Any]) -> ParsedTelegramMessage | None:
    """
    Parse a Telegram update into a structured message.

    Args:
        update: Raw Telegram webhook update dictionary

    Returns:
        ParsedTelegramMessage or None if the update cannot be parsed
    """
    message_obj = update.get("message") or update.get("edited_message")
    if not isinstance(message_obj, dict):
        return None
    message = cast(dict[str, Any], message_obj)

    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None

    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        return None

    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None

    from_user = message.get("from")
    if not isinstance(from_user, dict):
        return None
    telegram_id = from_user.get("id")
    if not isinstance(telegram_id, int):
        return None

    received_at = _parse_unix_seconds(message.get("date"))
    text = message.get("text") if isinstance(message.get("text"), str) else None
    caption = (
        message.get("caption") if isinstance(message.get("caption"), str) else None
    )

    file_kind: str | None = None
    file_id: str | None = None
    file_unique_id: str | None = None
    mime: str | None = None
    filename: str | None = None
    size: int | None = None

    if "document" in message and isinstance(message["document"], dict):
        doc = message["document"]
        file_kind = "document"
        file_id = doc.get("file_id") if isinstance(doc.get("file_id"), str) else None
        file_unique_id = (
            doc.get("file_unique_id")
            if isinstance(doc.get("file_unique_id"), str)
            else None
        )
        filename = (
            doc.get("file_name") if isinstance(doc.get("file_name"), str) else None
        )
        mime = doc.get("mime_type") if isinstance(doc.get("mime_type"), str) else None
        size = doc.get("file_size") if isinstance(doc.get("file_size"), int) else None
    elif "photo" in message:
        best = _pick_photo_variant(message.get("photo"))
        if best is not None:
            file_kind = "photo"
            file_id = (
                best.get("file_id") if isinstance(best.get("file_id"), str) else None
            )
            file_unique_id = (
                best.get("file_unique_id")
                if isinstance(best.get("file_unique_id"), str)
                else None
            )
            size = (
                best.get("file_size") if isinstance(best.get("file_size"), int) else None
            )

    return ParsedTelegramMessage(
        update_id=update_id,
        message_id=message_id,
        chat_id=chat_id,
        telegram_id=telegram_id,
        received_at=received_at,
        text=text,
        caption=caption,
        file_kind=file_kind,
        file_id=file_id,
        file_unique_id=file_unique_id,
        mime=mime,
        filename=filename,
        size=size,
    )
