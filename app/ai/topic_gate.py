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
    history_messages: list[dict[str, Any]] | None = None,
    memory_summary: str | None = None,
) -> tuple[bool, str | None, dict[str, Any], dict[str, str], int]:
    """
    Conservative topic gate with conversational context:
    - Returns on_topic=True unless we are highly confident it's off-topic.
    - Considers conversation history to understand context (e.g., user responding to bot's question).
    - Intended to be used before expensive processing to reduce unnecessary compute.

    Returns (on_topic, reason, response_json, response_headers, latency_ms).
    """
    if (
        hint_command
        and hint_command.strip()
        and hint_command not in {"/help", "/start"}
    ):
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
        "user",
        "invite",
        "code",
        "staff",
        "owner",
        "what can you do",
        "help",
        "capabilities",
        "hi",
        "hello",
    )
    if any(k in normalized for k in on_topic_keywords):
        return True, "keyword", {}, {}, 0

    gate_model = get_gate_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    # Build context from history for conversational awareness
    context_text = ""
    if history_messages:
        # Get last 5 messages to understand what bot asked / user responded
        recent = history_messages[-5:]
        context_parts = []
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                # Truncate long messages
                truncated = content[:200] + "..." if len(content) > 200 else content
                context_parts.append(f"{role}: {truncated}")
        if context_parts:
            context_text = "\n".join(context_parts)

    memory_text = ""
    if memory_summary:
        memory_text = memory_summary[:300]  # Truncate if needed

    system_prompt = (
        "You are a strict topic classifier for a restaurant/outlet operations assistant.\n"
        "This assistant helps with:\n"
        "- Restaurant/outlet operations and management\n"
        "- Database queries and management (users, restaurants, invite codes)\n"
        "- General questions about capabilities\n"
        "- Any business-related queries\n"
        "\n"
        "Classify the user's message as:\n"
        "- on_topic: clearly about restaurant/business operations OR responding to bot's question\n"
        "- off_topic: clearly unrelated AND not a response to any bot question\n"
        "- unsure: ambiguous\n"
        "\n"
        "CRITICAL RULES:\n"
        "1. If the bot asked a question and the user is responding to it, that's ON TOPIC.\n"
        "2. Phone numbers, names, confirmations given in response to bot questions are ON TOPIC.\n"
        "3. Questions about capabilities or 'what can you do' are ON TOPIC.\n"
        "4. Greetings like 'hi' or 'hello' are ON TOPIC.\n"
        "5. Be conservative. If unsure, choose 'unsure'.\n"
        "6. Consider the conversation context when classifying.\n"
        "\n"
        "Output ONLY valid JSON in this exact schema:\n"
        '{"verdict":"on_topic|off_topic|unsure","confidence":0.0,"reason":"short"}\n'
    )

    # Build user prompt with context
    user_prompt_parts = []
    if memory_text:
        user_prompt_parts.append(f"Memory context:\n{memory_text}")
    if context_text:
        user_prompt_parts.append(f"Recent conversation:\n{context_text}")
    user_prompt_parts.append(f"Current user message:\n{user_text}")
    user_prompt = "\n\n".join(user_prompt_parts)

    text, data, headers, latency_ms = (
        create_chat_completion_text_allow_empty_with_http_info(
            settings=gate_settings,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
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

    # Be lenient - especially if there's conversation context (user responding to bot's question)
    has_context = bool(history_messages) or bool(memory_summary)

    # Higher threshold when context exists - we want to be conservative about rejecting
    off_topic_threshold = 0.95 if has_context else 0.90

    if (
        verdict == "off_topic"
        and isinstance(confidence, float)
        and confidence >= off_topic_threshold
    ):
        return False, reason or "off_topic", data, headers, latency_ms

    return True, reason or (verdict or "on_topic"), data, headers, latency_ms
