from __future__ import annotations

import random
import re
from typing import Any

from app.ai.model_config import get_ack_model
from app.ai.openai_client import create_chat_completion_text_allow_empty_with_http_info
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages


def should_attempt_backchannel(
    *,
    messages: list[TelegramMessages],
    skip_probability: float = 0.35,
    rng: random.Random | None = None,
) -> bool:
    """
    Decide whether to attempt sending a backchannel ack for a session.

    This is intentionally cheap: use simple heuristics + a skip probability to avoid
    responding to every session.
    """
    if not messages:
        return False

    if skip_probability >= 1.0:
        return False
    if skip_probability <= 0.0:
        skip_probability = 0.0

    r = rng.random() if rng is not None else random.random()
    if r < skip_probability:
        return False

    last = messages[-1]
    last_text = (last.text or "").strip()
    last_caption = (last.caption or "").strip()
    content = last_text or last_caption
    if not content:
        # If they only sent media/empty, it often feels more natural to stay silent.
        return False

    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    if not normalized:
        return False

    # Don't ack acknowledgements; feels robotic.
    short_ack_like = {
        "ok",
        "okay",
        "k",
        "kk",
        "cool",
        "nice",
        "great",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "got it",
        "sounds good",
        "sure",
        "yes",
        "yeah",
        "yep",
        "no",
        "nope",
    }
    if normalized in short_ack_like:
        return False

    # Obvious bot-control commands shouldn't trigger casual backchannels.
    if normalized.startswith("/"):
        return False

    # Very long messages: backchannel is often helpful to signal "received".
    if len(normalized) >= 200:
        return True

    # Questions often benefit from an ack while we process.
    if "?" in normalized:
        return True

    # Default: attempt a backchannel.
    return True


def generate_backchannel_text(
    *,
    messages: list[TelegramMessages],
    settings: Settings,
) -> tuple[str | None, dict[str, Any], dict[str, str], int]:
    """
    Generate a short backchannel message (or silence) using the configured ack model.

    Returns (text_or_none, response_json, response_headers, latency_ms).
    """
    combined_lines: list[str] = []
    for msg in messages[-3:]:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
        if parts:
            combined_lines.append("\n".join(parts))

    user_text = "\n\n---\n\n".join(combined_lines).strip()
    if not user_text:
        user_text = "(no text; non-text update)"

    ack_model = get_ack_model(settings)
    ack_settings = (
        settings.model_copy(update={"openai_model": ack_model})
        if ack_model != settings.openai_model
        else settings
    )

    last = messages[-1] if messages else None
    last_content = ""
    if last is not None:
        last_text = (last.text or "").strip()
        last_caption = (last.caption or "").strip()
        last_content = (last_text or last_caption).strip()
    normalized_last = re.sub(r"\s+", " ", last_content.lower()).strip()
    looks_like_question = ("?" in last_content) or bool(
        re.match(
            r"^(what|which|where|when|why|how|did|do|does|can|could|is|are|have|has|will|would)\b",
            normalized_last,
        )
    )

    system_prompt = (
        "You are a tiny backchannel generator.\n"
        "Your job is to keep the conversation moving without answering.\n"
        "Rules:\n"
        "- Do NOT answer questions.\n"
        "- Do NOT provide details, steps, or advice.\n"
        "- If the user asks a question (status/info), prefer a \"checking\" style ack.\n"
        "- If the user gives an instruction or info, prefer a \"got it\" style ack.\n"
        "- Output either:\n"
        "  (A) a very short acknowledgement (1-4 words, no punctuation), OR\n"
        "  (B) a single space character to indicate silence.\n"
        "Examples of (A): checking | one sec | let me check | ok | got it | on it\n"
    )

    text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
        settings=ack_settings,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User said:\n{user_text}"},
        ],
        temperature=0.7,
    )

    if text is None:
        return None, data, headers, latency_ms

    cleaned = text.replace("\n", " ").strip()
    if not cleaned:
        return None, data, headers, latency_ms
    if cleaned == "__SILENCE__":
        return None, data, headers, latency_ms

    # Hard safety gates: keep it "backchannel only".
    if len(cleaned) > 48:
        return None, data, headers, latency_ms
    if len(cleaned.split()) > 6:
        return None, data, headers, latency_ms
    if any(ch.isdigit() for ch in cleaned):
        return None, data, headers, latency_ms

    # If they asked a question, prefer "checking" over an acknowledgement that can
    # read like a confirmation of action completion.
    if looks_like_question:
        ack_like = {
            "ok",
            "okay",
            "got it",
            "on it",
            "understood",
            "sure",
            "yep",
            "yeah",
        }
        if cleaned.lower() in ack_like:
            cleaned = random.choice(["checking", "one sec", "looking"])

    return cleaned, data, headers, latency_ms
