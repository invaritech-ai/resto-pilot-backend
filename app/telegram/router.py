"""Six-priority message dispatcher for Telegram updates.

Priority order (first match wins):
    1. Global Reset  — text matches reset words (home/menu/cancel/exit//start)
    2. Button        — callback_query present
    3. Command       — text starts with "/"
    4. File          — document or photo in message
    5. Pattern       — structured text (#N or qty+unit)
    6. LLM Fallback  — everything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.db.models.user import User

# ---------------------------------------------------------------------------
# Reset words — checked case-insensitively after strip.
# /start is listed here so it cannot fall through to the command handler.
# ---------------------------------------------------------------------------
_RESET_WORDS = {"home", "menu", "cancel", "exit", "/start"}

# ---------------------------------------------------------------------------
# Pattern regexes (Priority 5)
# ---------------------------------------------------------------------------
_HASH_NUM = re.compile(r"^#\d+$")
_QTY_UNIT = re.compile(r"^\d+(\.\d+)?\s+(kg|g|ltr|ml|case|each|pcs)$", re.IGNORECASE)


@dataclass
class RouteContext:
    db: Session
    user: User


@dataclass
class Handlers:
    reset:   Callable[[dict, RouteContext], Any]
    button:  Callable[[dict, RouteContext], Any]
    command: Callable[[dict, RouteContext], Any]
    file:    Callable[[dict, RouteContext], Any]
    pattern: Callable[[dict, RouteContext], Any]
    llm:     Callable[[dict, RouteContext], Any]


class Router:
    def __init__(self, handlers: Handlers) -> None:
        self.handlers = handlers

    def route(self, update: dict, ctx: RouteContext) -> Any:
        h = self.handlers
        msg = update.get("message", {})
        text: str = msg.get("text", "") or ""
        text_stripped = text.strip()
        text_lower = text_stripped.lower()

        # Priority 1 — Global Reset
        if text_lower in _RESET_WORDS:
            return h.reset(update, ctx)

        # Priority 2 — Button callback
        if "callback_query" in update:
            return h.button(update, ctx)

        # Priority 3 — Slash command
        if text_stripped.startswith("/"):
            return h.command(update, ctx)

        # Priority 4 — File upload
        if "document" in msg or "photo" in msg:
            return h.file(update, ctx)

        # Priority 5 — Structured pattern
        if _HASH_NUM.match(text_stripped) or _QTY_UNIT.match(text_stripped):
            return h.pattern(update, ctx)

        # Priority 6 — LLM fallback
        return h.llm(update, ctx)
