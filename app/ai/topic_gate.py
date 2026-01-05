from __future__ import annotations

import json
import re
from typing import Any

from app.ai.model_config import get_gate_model
from app.ai.openai_client import create_chat_completion_text_allow_empty_with_http_info
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages


def classify_on_topic(
    *,
    messages: list[TelegramMessages],
    settings: Settings,
    hint_command: str | None = None,
) -> tuple[bool, str | None, dict[str, Any], dict[str, str], int]:
    """
    Conservative topic gate:
    - Returns on_topic=True unless we are highly confident it's off-topic.
    - Intended to be used before expensive processing to reduce unnecessary compute.

    Returns (on_topic, reason, response_json, response_headers, latency_ms).
    """
    if hint_command and hint_command.strip() and hint_command not in {"/help", "/start"}:
        return True, "hint_command", {}, {}, 0

    if not messages:
        return True, "no_messages", {}, {}, 0

    # Strong on-topic signals: files often indicate invoices/docs/photos of receipts/menu/etc.
    if any(m.file_kind for m in messages):
        return True, "has_file", {}, {}, 0

    combined_lines: list[str] = []
    for msg in messages[-3:]:
        if isinstance(msg.text, str) and msg.text.strip():
            combined_lines.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            combined_lines.append(msg.caption.strip())

    user_text = "\n\n---\n\n".join(combined_lines).strip()
    normalized = re.sub(r"\s+", " ", user_text.lower()).strip()
    if len(normalized) < 5:
        return True, "too_short", {}, {}, 0

    # Positive keyword heuristic only (avoid false off-topic).
    on_topic_keywords = (
        "restaurant",
        "outlet",
        "menu",
        "invoice",
        "bill",
        "gst",
        "inventory",
        "stock",
        "supplier",
        "purchase",
        "recipe",
        "portion",
        "kitchen",
        "pos",
        "swiggy",
        "zomato",
        "order",
        "orders",
        "pricing",
        "price",
        "discount",
        "food cost",
        "cogs",
        "wastage",
        "waste",
    )
    if any(k in normalized for k in on_topic_keywords):
        return True, "keyword", {}, {}, 0

    gate_model = get_gate_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    system_prompt = (
        "You are a strict topic classifier for a restaurant/outlet operations assistant.\n"
        "Classify the user's message as:\n"
        "- on_topic: clearly about restaurant/outlet operations\n"
        "- off_topic: clearly unrelated\n"
        "- unsure: ambiguous\n"
        "IMPORTANT: Be conservative. If unsure, choose 'unsure'.\n"
        "Output ONLY valid JSON in this exact schema:\n"
        '{"verdict":"on_topic|off_topic|unsure","confidence":0.0,"reason":"short"}\n'
    )

    text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
        settings=gate_settings,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User message:\n{user_text}"},
        ],
        temperature=0.0,
    )

    if text is None:
        return True, "empty", data, headers, latency_ms

    verdict: str | None = None
    confidence: float | None = None
    reason: str | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            v = parsed.get("verdict")
            c = parsed.get("confidence")
            r = parsed.get("reason")
            verdict = v if isinstance(v, str) else None
            confidence = float(c) if isinstance(c, (int, float)) else None
            reason = r if isinstance(r, str) else None
    except Exception:
        return True, "unparseable", data, headers, latency_ms

    if verdict == "off_topic" and isinstance(confidence, float) and confidence >= 0.85:
        return False, reason or "off_topic", data, headers, latency_ms

    return True, reason or (verdict or "on_topic"), data, headers, latency_ms

