"""
ACK message generation with LLM for variety.

Generates varied acknowledgments like "Got it", "On it", "Checking...", etc.
Uses a cheap/fast model for instant response.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.model_config import get_ack_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings

logger = logging.getLogger(__name__)

ACK_SYSTEM_PROMPT = """You are a restaurant management assistant that generates short acknowledgment messages.

Task:
Acknowledge that the user's message was received.

Output rules:
- 1-3 words only
- Natural, professional, and neutral tone
- No emojis
- No punctuation unless required for clarity
- No repetition within a single session if possible

Response style:
- Use concise acknowledgments such as:
  - "Got it"
  - "On it"
  - "Checking"
  - "One moment"
  - "Looking now"

Special case:
- If the user sends a file, respond with:
  - "File received"

Output:
Return ONLY the acknowledgment text.
"""


def generate_ack(
    *,
    settings: Settings,
    message_text: str,
    has_file: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Generate varied acknowledgment using ACK model.

    Args:
        settings: Application settings
        message_text: User's message text
        has_file: Whether message has an attached file

    Returns:
        (ack_text, telemetry) or (None, None) on failure
        telemetry contains: model, generation_id, usage, latency_ms
    """
    model = get_ack_model(settings)
    ack_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    context = "file" if has_file else "text"
    prompt = f"Generate acknowledgment for user {context} message: {message_text[:50]}"

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=ack_settings,
            messages=[
                {"role": "system", "content": ACK_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,  # Variety
            purpose="ack",
            extra_body={"max_tokens": 15},  # Keep it very short
        )
    except OpenAIError as exc:
        logger.warning("ack_llm_failed error=%r", exc)
        return None, None

    # Extract response
    try:
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )
        if not content:
            return None, None

        # Build telemetry
        usage = extract_openrouter_usage(data)
        generation_id = extract_openrouter_generation_id(headers=headers, data=data)
        model_used = data.get("model", model)

        telemetry = {
            "model": model_used if isinstance(model_used, str) else model,
            "latency_ms": latency_ms,
            "generation_id": generation_id,
            "usage": usage if isinstance(usage, dict) else {},
        }

        return content, telemetry
    except Exception as exc:
        logger.warning("ack_llm_parse_failed error=%r", exc)
        return None, None
