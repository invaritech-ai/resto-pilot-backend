"""
Tool-calling resolver for the static bot.

Uses a single LLM with function calling to resolve intent, fetch data,
and compose the final user-facing response.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.model_config import get_response_model
from app.ai.openai_client import OpenAIError, chat_completions_create_with_http_info
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.tools import TOOLS as BASE_TOOLS, tools_to_openai_schema
from app.ai.db_tools import create_db_tools
from app.conversation import responses
from app.core.config import Settings
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class LLMCallTelemetry:
    model: str
    latency_ms: int
    generation_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResolutionResult:
    response_text: str
    llm_calls: list[LLMCallTelemetry] = field(default_factory=list)
    context_update: dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0


RESOLVER_SYSTEM_PROMPT = """You are a restaurant management assistant.
Use tools to read or update data. Do NOT guess IDs.
If you need a restaurant or supplier, call list tools first.
If a tool returns an error, explain the issue and ask a short follow-up.
Never reveal raw UUIDs or internal IDs.
Keep responses short (1-3 sentences) or short bullet lists when listing items.
Do not pass empty strings for optional fields unless the user explicitly asks to clear them.
If the user asks for help about a specific topic, call get_help_topic.
If the user asks for menu/options/paths, call get_menu_paths.
If there is a pending action and the user says yes/no, call the relevant tool to confirm or cancel.
When listing staff, include the outlet name if provided by the tool.
"""


def _format_history(history: list[dict[str, str]] | None, limit: int = 12) -> str | None:
    if not history:
        return None
    lines: list[str] = []
    for msg in history[-limit:]:
        role = msg.get("role", "")
        content = msg.get("content", "")[:200]
        if role and content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else None


def _load_restaurant_roles(db: Session, user_id: uuid.UUID) -> tuple[str, dict[str, str]]:
    rows = db.scalars(
        select(RestaurantUser).where(
            RestaurantUser.user_id == user_id,
            RestaurantUser.status != "removed",
        )
    ).all()
    roles = {str(r.restaurant_id): r.role for r in rows}
    actor_role = "owner" if any(r.role == "owner" for r in rows) else "staff"
    return actor_role, roles


def _parse_tool_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_with_tools(
    *,
    db: Session,
    user: User,
    message_text: str,
    history: list[dict[str, str]] | None,
    active_restaurant_id: str | None,
    active_supplier_id: str | None,
    pending_action: dict[str, Any] | None = None,
    settings: Settings,
    chat_id: int | None,
    session_id: uuid.UUID | None,
    max_steps: int = 6,
) -> ToolResolutionResult:
    context_info: dict[str, str] = {}
    if active_restaurant_id:
        context_info["active_restaurant_id"] = active_restaurant_id
    if active_supplier_id:
        context_info["active_supplier_id"] = active_supplier_id
    if pending_action:
        context_info["pending_action"] = pending_action.get("type")

    user_prompt_parts: list[str] = []
    if context_info:
        user_prompt_parts.append(f"Active context: {json.dumps(context_info)}")
    history_text = _format_history(history)
    if history_text:
        user_prompt_parts.append(f"Recent conversation:\n{history_text}")
    user_prompt_parts.append(f"User message: {message_text}")
    user_prompt = "\n\n".join(user_prompt_parts)

    model = get_response_model(settings)
    resolver_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    actor_role, restaurant_roles = _load_restaurant_roles(db, user.id)
    tools = create_db_tools(
        db=db,
        user_id=user.id,
        actor_role=actor_role,
        restaurant_roles=restaurant_roles,
        pending_action=pending_action,
        user_message=message_text,
        chat_id=chat_id,
        session_id=session_id,
    )
    merged_tools = {**BASE_TOOLS, **tools}
    tool_schema = tools_to_openai_schema(merged_tools)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": RESOLVER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    llm_calls: list[LLMCallTelemetry] = []
    tool_calls_count = 0
    last_restaurant_id: str | None = None
    last_supplier_id: str | None = None
    context_update: dict[str, Any] = {}

    for _ in range(max_steps):
        try:
            data, headers, latency_ms = chat_completions_create_with_http_info(
                settings=resolver_settings,
                messages=messages,
                tools=tool_schema,
                temperature=0.2,
                extra_body={"reasoning": {"effort": "low"}},
            )
        except OpenAIError as exc:
            logger.exception("tool_resolver_failed", extra={"error": str(exc)})
            return ToolResolutionResult(
                response_text=responses.ERROR_GENERIC,
                llm_calls=llm_calls,
            )

        usage = extract_openrouter_usage(data)
        generation_id = extract_openrouter_generation_id(headers=headers, data=data)
        model_used = data.get("model", model)
        llm_calls.append(
            LLMCallTelemetry(
                model=model_used if isinstance(model_used, str) else model,
                latency_ms=latency_ms,
                generation_id=generation_id,
                usage=usage if isinstance(usage, dict) else {},
            )
        )

        message = data.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls")
        content = message.get("content")

        if tool_calls:
            messages.append(message)
            for call in tool_calls:
                tool_calls_count += 1
                call_id = call.get("id")
                fn = call.get("function", {})
                name = fn.get("name")
                args = _parse_tool_args(fn.get("arguments"))
                if isinstance(args.get("restaurant_id"), str):
                    last_restaurant_id = args["restaurant_id"].strip()
                if isinstance(args.get("supplier_id"), str):
                    last_supplier_id = args["supplier_id"].strip()

                tool = merged_tools.get(name)
                if not tool:
                    result = "Error: Unknown tool."
                else:
                    result = tool.handler(args)
                tool_payload = _parse_tool_args(result)
                tool_context = tool_payload.get("context_update")
                if isinstance(tool_context, dict):
                    context_update.update(tool_context)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result,
                    }
                )
            continue

        if isinstance(content, str) and content.strip():
            if last_restaurant_id:
                context_update["active_restaurant_id"] = last_restaurant_id
            if last_supplier_id:
                context_update["active_supplier_id"] = last_supplier_id
            return ToolResolutionResult(
                response_text=content.strip(),
                llm_calls=llm_calls,
                context_update=context_update,
                tool_calls=tool_calls_count,
            )

        break

    return ToolResolutionResult(
        response_text=responses.ERROR_GENERIC,
        llm_calls=llm_calls,
        tool_calls=tool_calls_count,
    )
