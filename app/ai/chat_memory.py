from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.model_config import get_gate_model
from app.ai.openai_client import create_chat_completion_text_allow_empty_with_http_info
from app.core.config import Settings
from app.db.models.telegram_chat_memory import TelegramChatMemory
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages


def load_chat_memory_summary(*, db: Session, chat_id: int) -> str | None:
    row = db.scalar(select(TelegramChatMemory).where(TelegramChatMemory.chat_id == chat_id))
    if row is None:
        return None
    text = row.summary_text
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


def load_chat_history_messages(
    *,
    db: Session,
    chat_id: int,
    exclude_session_id: Any | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Returns OpenAI-format messages: [{"role": "...", "content": "..."}]
    Includes both user + assistant messages. Best-effort time ordering.
    """
    safe_limit = max(1, min(200, int(limit)))

    incoming = list(
        db.scalars(
            select(TelegramMessages)
            .where(
                TelegramMessages.chat_id == chat_id,
                TelegramMessages.session_id != exclude_session_id,
            )
            .order_by(TelegramMessages.received_at.desc(), TelegramMessages.message_id.desc())
            .limit(safe_limit)
        )
    )
    outgoing = list(
        db.scalars(
            select(TelegramOutgoingMessages)
            .where(
                TelegramOutgoingMessages.chat_id == chat_id,
                TelegramOutgoingMessages.session_id != exclude_session_id,
            )
            .order_by(TelegramOutgoingMessages.sent_at.desc())
            .limit(safe_limit)
        )
    )

    merged: list[tuple[dt.datetime, str, str]] = []
    for msg in incoming:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
        if msg.file_kind:
            parts.append(f"[file] {msg.file_kind}")
        content = "\n".join(parts).strip()
        if not content:
            continue
        at = msg.received_at if isinstance(msg.received_at, dt.datetime) else dt.datetime.now(dt.UTC)
        merged.append((at, "user", content[:1000]))

    for msg in outgoing:
        content = msg.text if isinstance(msg.text, str) else ""
        if not content.strip():
            continue
        at = msg.sent_at if isinstance(msg.sent_at, dt.datetime) else dt.datetime.now(dt.UTC)
        kind = msg.kind if isinstance(msg.kind, str) and msg.kind else "assistant"
        merged.append((at, "assistant", f"[{kind}] {content}".strip()[:1000]))

    merged.sort(key=lambda t: t[0])
    merged = merged[-safe_limit:]

    return [{"role": role, "content": content} for (_at, role, content) in merged]


def update_chat_memory_summary(
    *,
    settings: Settings,
    previous_summary: str | None,
    history_messages: list[dict[str, Any]],
    max_chars: int = 1200,
) -> tuple[str | None, dict[str, Any], dict[str, str], int]:
    """
    Summarize chat context into a compact memory string.

    Uses the gate model (cheap) to avoid extra configuration.
    """
    gate_model = get_gate_model(settings)
    summarizer_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    prev = (previous_summary or "").strip()
    history_text = "\n".join(
        f"{m.get('role')}: {m.get('content')}"
        for m in history_messages[-50:]
        if isinstance(m, dict)
    ).strip()
    if not history_text:
        history_text = "(no history)"

    system_prompt = (
        "You are a memory summarizer for a restaurant/outlet operations assistant.\n"
        "Write a compact, factual memory of the conversation that will help future turns.\n"
        "Rules:\n"
        f"- Output MUST be <= {int(max_chars)} characters.\n"
        "- Include stable facts: outlet names, user preferences, ongoing tasks, constraints.\n"
        "- Exclude chit-chat and acknowledgements.\n"
        "- Do NOT provide advice or plans.\n"
        "- Output plain text only."
    )

    user_prompt = (
        f"Previous memory (may be empty):\n{prev or '(empty)'}\n\n"
        f"Recent conversation:\n{history_text}\n\n"
        "Write updated memory:"
    )

    text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
        settings=summarizer_settings,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    if text is None:
        return None, data, headers, latency_ms

    cleaned = text.strip()
    if not cleaned:
        return None, data, headers, latency_ms

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
    return cleaned, data, headers, latency_ms

