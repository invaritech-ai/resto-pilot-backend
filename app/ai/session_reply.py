from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.tools import TOOLS, openai_tools_schema
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages
from app.telegram.bot_api import get_file_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionReply:
    text: str
    model: str


def _require_dict(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenAIError(f"Unexpected OpenAI response shape at {context}: {value!r}")
    return cast(dict[str, Any], value)


def _data_url(*, mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_image_inputs(
    *, messages: list[TelegramMessages], settings: Settings
) -> list[dict[str, Any]]:
    image_inputs: list[dict[str, Any]] = []
    max_images = 3
    max_bytes = 2_000_000

    for msg in messages:
        if len(image_inputs) >= max_images:
            break
        if not msg.file_id or not msg.file_kind:
            continue
        if msg.file_kind not in {"photo", "document"}:
            continue
        mime = msg.mime or "image/jpeg"
        if not mime.startswith("image/"):
            continue
        if isinstance(msg.size, int) and msg.size > max_bytes:
            continue

        try:
            data = get_file_bytes(file_id=msg.file_id, settings=settings, max_bytes=max_bytes)
        except Exception as exc:
            logger.exception(
                "telegram_file_download_failed",
                extra={"error": repr(exc), "file_kind": msg.file_kind},
            )
            continue

        if len(data) > max_bytes:
            continue
        image_inputs.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(mime=mime, data=data)},
            }
        )

    return image_inputs


def generate_session_reply(
    *,
    messages: list[TelegramMessages],
    hint_command: str | None,
    settings: Settings,
) -> SessionReply:
    """
    Generate a single assistant reply for a batch ("session") of Telegram messages.

    This is intentionally simple and may be upgraded later (OCR, tools, routing, etc.).
    """
    combined_lines: list[str] = []
    for msg in messages:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(f"[caption] {msg.caption.strip()}")
        if msg.file_kind:
            desc = msg.file_kind
            if msg.filename:
                desc += f" filename={msg.filename}"
            if msg.mime:
                desc += f" mime={msg.mime}"
            parts.append(f"[file] {desc}")
        if parts:
            combined_lines.append("\n".join(parts))

    user_text = "\n\n---\n\n".join(combined_lines).strip()
    if not user_text:
        user_text = "User sent an empty update."

    image_inputs = _extract_image_inputs(messages=messages, settings=settings)

    system_prompt = (
        "You are Resto Pilot, an assistant for restaurant operations.\n"
        "Be concise and practical.\n"
        "You can call tools if needed (tool: get_current_datetime).\n"
        "If the content is unrelated to restaurant operations, politely say so and ask what they need.\n"
        "If the user provides an invoice/bill image, try to extract key details and ask one follow-up only if needed."
    )
    if hint_command:
        system_prompt += f"\nSession hint command: {hint_command}"

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    content.extend(image_inputs)

    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    tools = openai_tools_schema()
    max_tool_rounds = 3
    text: str | None = None

    for _round in range(max_tool_rounds + 1):
        try:
            data = chat_completions_create(
                settings=settings,
                messages=conversation,
                tools=tools,
                temperature=0.2,
            )
        except OpenAIError:
            raise
        except Exception as exc:
            raise OpenAIError(f"Failed to generate session reply: {exc}") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIError(f"Unexpected OpenAI response shape at choices: {data}")
        choice0 = _require_dict(choices[0], context="choices[0]")
        msg = _require_dict(choice0.get("message"), context="choices[0].message")

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            conversation.append(msg)
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                args_raw = function.get("arguments")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    continue

                tool = TOOLS.get(name)
                if tool is None:
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"ERROR: unknown tool '{name}'",
                        }
                    )
                    continue

                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}

                result = tool.handler(args)
                conversation.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )
            continue

        content_text = msg.get("content")
        if isinstance(content_text, str) and content_text.strip():
            text = content_text.strip()
            break

    if text is None:
        raise OpenAIError("Model did not return a final message")

    return SessionReply(text=text, model=settings.openai_model)


def generate_session_reply_with_metrics(
    *,
    messages: list[TelegramMessages],
    hint_command: str | None,
    settings: Settings,
) -> tuple[SessionReply, dict[str, Any]]:
    """
    Like generate_session_reply(), but returns aggregated metrics across tool rounds.

    Metrics include:
    - call_count
    - latency_ms_total
    - prompt_tokens_total / completion_tokens_total / total_tokens_total (if available)
    - cost_usd_total (if available from response usage)
    - openrouter_generation_ids (best-effort)
    """
    combined_lines: list[str] = []
    for msg in messages:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(f"[caption] {msg.caption.strip()}")
        if msg.file_kind:
            desc = msg.file_kind
            if msg.filename:
                desc += f" filename={msg.filename}"
            if msg.mime:
                desc += f" mime={msg.mime}"
            parts.append(f"[file] {desc}")
        if parts:
            combined_lines.append("\n".join(parts))

    user_text = "\n\n---\n\n".join(combined_lines).strip()
    if not user_text:
        user_text = "User sent an empty update."

    image_inputs = _extract_image_inputs(messages=messages, settings=settings)

    system_prompt = (
        "You are Resto Pilot, an assistant for restaurant operations.\n"
        "Be concise and practical.\n"
        "You can call tools if needed (tool: get_current_datetime).\n"
        "If the content is unrelated to restaurant operations, politely say so and ask what they need.\n"
        "If the user provides an invoice/bill image, try to extract key details and ask one follow-up only if needed."
    )
    if hint_command:
        system_prompt += f"\nSession hint command: {hint_command}"

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    content.extend(image_inputs)

    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    tools = openai_tools_schema()
    max_tool_rounds = 3

    metrics: dict[str, Any] = {
        "call_count": 0,
        "latency_ms_total": 0,
        "prompt_tokens_total": 0,
        "completion_tokens_total": 0,
        "total_tokens_total": 0,
        "cost_usd_total": 0.0,
        "openrouter_generation_ids": [],
    }

    text: str | None = None
    last_model: str = settings.openai_model

    for _round in range(max_tool_rounds + 1):
        try:
            data, headers, latency_ms = chat_completions_create_with_http_info(
                settings=settings,
                messages=conversation,
                tools=tools,
                temperature=0.2,
            )
        except OpenAIError:
            raise
        except Exception as exc:
            raise OpenAIError(f"Failed to generate session reply: {exc}") from exc

        metrics["call_count"] += 1
        metrics["latency_ms_total"] += int(latency_ms)

        usage = extract_openrouter_usage(data)
        if usage is not None:
            metrics["prompt_tokens_total"] += int(usage["prompt_tokens"])
            metrics["completion_tokens_total"] += int(usage["completion_tokens"])
            metrics["total_tokens_total"] += int(usage["total_tokens"])

        usage_obj = data.get("usage")
        if isinstance(usage_obj, dict) and isinstance(usage_obj.get("cost"), (int, float)):
            metrics["cost_usd_total"] += float(usage_obj["cost"])

        generation_id = extract_openrouter_generation_id(headers=headers, data=data)
        if generation_id is not None:
            metrics["openrouter_generation_ids"].append(generation_id)

        if isinstance(data.get("model"), str):
            last_model = cast(str, data["model"])

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIError(f"Unexpected OpenAI response shape at choices: {data}")
        choice0 = _require_dict(choices[0], context="choices[0]")
        msg = _require_dict(choice0.get("message"), context="choices[0].message")

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            conversation.append(msg)
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                args_raw = function.get("arguments")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    continue

                tool = TOOLS.get(name)
                if tool is None:
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"ERROR: unknown tool '{name}'",
                        }
                    )
                    continue

                try:
                    args = (
                        json.loads(args_raw)
                        if isinstance(args_raw, str) and args_raw
                        else {}
                    )
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}

                result = tool.handler(args)
                conversation.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )
            continue

        content_text = msg.get("content")
        if isinstance(content_text, str) and content_text.strip():
            text = content_text.strip()
            break

    if text is None:
        raise OpenAIError("Model did not return a final message")

    return SessionReply(text=text, model=last_model), metrics
