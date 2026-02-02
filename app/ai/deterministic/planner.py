from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.ai.model_config import get_reasoning_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.deterministic.schemas import PlannerClarify, PlannerCallTool, PlannerDecision, PlannerTelemetry
from app.core.config import Settings

logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """You are the Planner for a restaurant management Telegram bot.

You MUST output ONLY valid JSON.

You must choose exactly one of:
1) {"action":"clarify", ...}
2) {"action":"call_tool", ...}

Hard rules:
- Call at most ONE tool.
- Never output internal IDs/UUIDs. Users don't use IDs.
- Prefer asking a clarification question instead of guessing.
- For writes/link/unlink/rename: if an entity match could be ambiguous, ask a numbered clarification question.
- Suppliers cannot be created manually. If a supplier is missing, instruct the user to upload a supplier price list to add it.

When clarifying, prefer numbered choices when possible and ask the user to reply with a number (1, 2, 3...).
Keep questions short.
"""


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _planner_tool_schema() -> list[dict[str, Any]]:
    # Force the model to "call" a single function with JSON args.
    return [
        {
            "type": "function",
            "function": {
                "name": "planner_decision",
                "description": "Return exactly one planner decision (clarify OR call_tool).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["clarify", "call_tool"]},
                        "clarify_kind": {"type": "string"},
                        "question": {"type": "string"},
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["label", "text"],
                                "additionalProperties": False,
                            },
                        },
                        "tool": {"type": "string"},
                        "args": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def _extract_tool_call_args(data: dict[str, Any]) -> dict[str, Any] | None:
    try:
        message = data.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return None
        fn = (tool_calls[0] or {}).get("function") or {}
        if fn.get("name") != "planner_decision":
            return None
        raw = fn.get("arguments")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return _parse_json(raw.strip())
    except Exception:
        return None


def plan_next_action(
    *,
    settings: Settings,
    message_text: str,
    recent_turns: list[dict[str, str]] | None,
    context: dict[str, Any] | None,
    available_tools: list[dict[str, Any]],
    hard_rules: dict[str, Any] | None = None,
) -> tuple[PlannerDecision, PlannerTelemetry | None]:
    model = get_reasoning_model(settings)
    planner_settings = settings.model_copy(update={"openai_model": model}) if model != settings.openai_model else settings

    payload = {
        "message_text": message_text,
        "recent_turns": recent_turns or [],
        "context": context or {},
        "available_tools": available_tools,
        "hard_rules": hard_rules
        or {
            "one_tool_max": True,
            "no_ids": True,
            "no_manual_supplier_creation": True,
            "strict_resolution_for_writes": True,
        },
    }

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=planner_settings,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, indent=2)},
            ],
            temperature=0.1,
            tools=_planner_tool_schema(),
            # Keep provider defaults ("auto"). Some OpenRouter providers reject forced tool_choice.
            extra_body={"max_tokens": 500},
        )
    except OpenAIError as exc:
        logger.exception("planner_llm_failed", extra={"error": str(exc)})
        # Deterministic fallback: ask for clarification.
        return (
            PlannerClarify(
                action="clarify",
                clarify_kind="unknown",
                question="I couldn't understand that. What would you like to do?",
                choices=None,
            ),
            None,
        )

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", model)
    telemetry = PlannerTelemetry(
        model=model_used if isinstance(model_used, str) else model,
        latency_ms=latency_ms,
        generation_id=generation_id,
        usage=usage if isinstance(usage, dict) else {},
    )

    parsed = _extract_tool_call_args(data)
    if not parsed:
        return (
            PlannerClarify(
                action="clarify",
                clarify_kind="unknown",
                question="Please rephrase your request in one sentence.",
                choices=None,
            ),
            telemetry,
        )

    try:
        decision: PlannerDecision
        if parsed.get("action") == "call_tool":
            decision = PlannerCallTool.model_validate(parsed)
        else:
            decision = PlannerClarify.model_validate(parsed)
        return decision, telemetry
    except ValidationError:
        return (
            PlannerClarify(
                action="clarify",
                clarify_kind="unknown",
                question="Please clarify what you want to do.",
                choices=None,
            ),
            telemetry,
        )
