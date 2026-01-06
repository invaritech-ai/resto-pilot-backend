from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ai.model_config import get_gate_model
from app.ai.openai_client import create_chat_completion_text_allow_empty_with_http_info
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages
from app.policies.db_allowlist import DB_ALLOWLIST
from app.schemas.db_action import DBAction, parse_db_action


@dataclass(frozen=True)
class DBIntentResult:
    is_db_action: bool
    reason: str | None
    confidence: float | None
    data: dict[str, Any]
    headers: dict[str, str]
    latency_ms: int


@dataclass(frozen=True)
class DBActionResult:
    action: DBAction | None
    errors: list[str]
    raw_text: str | None
    data: dict[str, Any]
    headers: dict[str, str]
    latency_ms: int


def classify_db_intent(
    *,
    messages: list[TelegramMessages],
    settings: Settings,
) -> DBIntentResult:
    user_text = _combined_user_text(messages=messages)
    if not user_text:
        return DBIntentResult(False, "no_text", None, {}, {}, 0)

    gate_model = get_gate_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    system_prompt = (
        "You are a strict classifier that decides if the user is requesting a database action.\n"
        "Database actions include reading or changing records in these tables:\n"
        "- users, restaurants, restaurant_users, invite_codes\n"
        "If the user is just chatting or asking questions without requesting DB access, return false.\n"
        "Output ONLY valid JSON in this exact schema:\n"
        '{"is_db_action":true|false,"confidence":0.0,"reason":"short"}\n'
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
        return DBIntentResult(False, "empty", None, data, headers, latency_ms)

    parsed = _parse_json(text)
    if not isinstance(parsed, dict):
        return DBIntentResult(False, "unparseable", None, data, headers, latency_ms)

    is_db_action = parsed.get("is_db_action")
    confidence = parsed.get("confidence")
    reason = parsed.get("reason")

    is_db_action_bool = bool(is_db_action)
    conf_float = float(confidence) if isinstance(confidence, (int, float)) else None
    reason_str = reason if isinstance(reason, str) else None

    if is_db_action_bool and isinstance(conf_float, float) and conf_float >= 0.7:
        return DBIntentResult(True, reason_str, conf_float, data, headers, latency_ms)
    return DBIntentResult(False, reason_str, conf_float, data, headers, latency_ms)


def extract_db_action(
    *,
    messages: list[TelegramMessages],
    settings: Settings,
    actor_role: str,
) -> DBActionResult:
    user_text = _combined_user_text(messages=messages)
    if not user_text:
        return DBActionResult(None, ["no_text"], None, {}, {}, 0)

    allowlist_summary = _allowlist_summary(actor_role=actor_role)
    system_prompt = (
        "You are extracting a structured DB action.\n"
        "Return ONLY valid JSON matching this schema:\n"
        '{\n'
        '  "action_id":"string",\n'
        '  "intent":"short summary",\n'
        '  "crud":"read|create|update|delete",\n'
        '  "table":"users|restaurants|restaurant_users|invite_codes",\n'
        '  "role":"owner|staff",\n'
        '  "scope":"self|owned_restaurant|restaurant_owner",\n'
        '  "filters": {"by_user_id": "...", "by_restaurant_id": "...", "by_invite_code": "...", "by_restaurant_user_id": "..."},\n'
        '  "values": {"column": "value"},\n'
        '  "columns": ["column", "column"],\n'
        '  "needs_confirmation": true|false,\n'
        '  "confidence": 0.0,\n'
        '  "errors": ["string"]\n'
        "}\n"
        "Only use tables/columns allowed for this role:\n"
        f"{allowlist_summary}\n"
        "If unsure, set confidence low and explain in errors.\n"
    )

    text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
        settings=settings,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User message:\n{user_text}"},
        ],
        temperature=0.0,
    )

    if text is None:
        return DBActionResult(None, ["empty"], None, data, headers, latency_ms)

    parsed = _parse_json(text)
    if not isinstance(parsed, dict):
        return DBActionResult(None, ["unparseable"], text, data, headers, latency_ms)

    action, errors = parse_db_action(parsed)
    if action is None:
        return DBActionResult(None, errors, text, data, headers, latency_ms)

    return DBActionResult(action, [], text, data, headers, latency_ms)


def _combined_user_text(*, messages: list[TelegramMessages], limit: int = 5) -> str:
    parts: list[str] = []
    for msg in messages[-limit:]:
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
    return "\n\n".join(parts).strip()


def _allowlist_summary(*, actor_role: str) -> str:
    role_allowlist = DB_ALLOWLIST.get(actor_role, {})
    lines: list[str] = []
    for table, crud_map in role_allowlist.items():
        for crud, spec in crud_map.items():
            columns = spec.get("columns", [])
            scope = spec.get("scope")
            lines.append(f"- {table}.{crud}: columns={columns}, scope={scope}")
    return "\n".join(lines) if lines else "- none"


def _parse_json(text: str) -> dict[str, Any] | None:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned
