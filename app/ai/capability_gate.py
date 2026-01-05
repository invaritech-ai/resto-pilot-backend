from __future__ import annotations

import re

from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages


def get_enabled_capabilities(*, settings: Settings) -> set[str]:
    raw = settings.enabled_capabilities_csv or ""
    items = [part.strip().lower() for part in raw.split(",")]
    return {item for item in items if item}


def _combined_user_text(*, messages: list[TelegramMessages]) -> str:
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
    return "\n".join(parts)


def is_outlet_list_request(*, messages: list[TelegramMessages]) -> bool:
    text = _combined_user_text(messages=messages)
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if not normalized:
        return False
    patterns = [
        r"\bwhat outlets do i have\b",
        r"\blist (all )?outlets\b",
        r"\bshow (me )?(all )?outlets\b",
        r"\boutlet list\b",
    ]
    return any(re.search(p, normalized) for p in patterns)


def classify_capability(
    *,
    messages: list[TelegramMessages],
    hint_command: str | None,
    settings: Settings,
) -> tuple[bool, str]:
    """
    Hard capability gate for the main processing bot.

    Returns (supported, reject_text). If supported is True, reject_text is unused.
    """
    enabled = get_enabled_capabilities(settings=settings)
    if "inventory" not in enabled:
        return (
            False,
            "Inventory updates are not enabled right now. Please try again later.",
        )

    if hint_command == "/inventory":
        return True, ""

    text = _combined_user_text(messages=messages)
    normalized = re.sub(r"\s+", " ", text.lower()).strip()

    inventory_keywords = {
        "inventory",
        "stock",
        "restock",
        "add",
        "remove",
        "deduct",
        "increase",
        "decrease",
        "qty",
        "quantity",
        "kg",
        "g",
        "litre",
        "liter",
        "l",
        "pcs",
        "piece",
        "pieces",
        "pack",
        "box",
        "carton",
    }
    if any(word in normalized for word in inventory_keywords):
        return True, ""

    return (
        False,
        "Only inventory updates are supported right now. What inventory change do you want to make (item + quantity) and for which outlet?",
    )


def inventory_outlet_list_refusal() -> str:
    return "I can't access your outlet list. What's the exact outlet name?"
