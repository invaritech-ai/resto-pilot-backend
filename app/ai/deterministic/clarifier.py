from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.model_config import get_clarification_model
from app.ai.openai_client import OpenAIError, create_chat_completion_text_allow_empty_with_http_info
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings

logger = logging.getLogger(__name__)


CLARIFIER_SYSTEM_PROMPT = """You are the Clarifier for a restaurant management Telegram bot.
Rewrite the question to be short and clear.

Rules:
- Output ONLY valid JSON: {"text": "..."}.
- If choices are provided, include them in the text as numbered lines (1) ..., 2) ...).
- Do not invent new choices or facts.
- Keep it terse.
"""


def clarify_text(
    *,
    settings: Settings,
    clarify_kind: str | None,
    question: str,
    choices: list[dict[str, str]] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    model = get_clarification_model(settings)
    clarifier_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    payload = {
        "clarify_kind": clarify_kind,
        "question": question,
        "choices": choices,
    }

    try:
        try:
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=clarifier_settings,
                messages=[
                    {"role": "system", "content": CLARIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                purpose="clarifier",
                extra_body={"max_tokens": 200},
            )
        except TypeError as exc:
            # Backwards-compat: if a worker is running with an older openai_client loaded.
            if "unexpected keyword argument 'purpose'" not in str(exc):
                raise
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=clarifier_settings,
                messages=[
                    {"role": "system", "content": CLARIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                extra_body={"max_tokens": 200},
            )
    except OpenAIError as exc:
        logger.exception("clarifier_failed", extra={"error": str(exc)})
        return None, None

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    telemetry = {
        "model": data.get("model", model),
        "latency_ms": latency_ms,
        "generation_id": generation_id,
        "usage": usage if isinstance(usage, dict) else {},
    }

    if not isinstance(text, str) or not text.strip():
        return None, telemetry

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str) and parsed["text"].strip():
            return parsed["text"].strip(), telemetry
    except Exception:
        pass

    return None, telemetry
