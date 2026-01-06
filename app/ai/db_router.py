from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ai.model_config import get_gate_model
from app.ai.openai_client import create_chat_completion_text_allow_empty_with_http_info
from app.ai.db_schema import get_table_column_descriptions
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
    is_capability_query: bool = False


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
    actor_role: str = "staff",
) -> DBIntentResult:
    user_text = _combined_user_text(messages=messages)
    if not user_text:
        return DBIntentResult(False, "no_text", None, {}, {}, 0, False)

    # Check for capability queries
    capability_keywords = [
        "what can you do",
        "what tables",
        "show permissions",
        "what are my permissions",
        "what can i do",
        "list tables",
        "show capabilities",
    ]
    normalized_text = user_text.lower().strip()
    is_capability_query = any(
        keyword in normalized_text for keyword in capability_keywords
    )

    if is_capability_query:
        return DBIntentResult(True, "capability_query", 1.0, {}, {}, 0, True)

    gate_model = get_gate_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": gate_model})
        if gate_model != settings.openai_model
        else settings
    )

    # Get schema descriptions for the role
    schema_description = get_table_column_descriptions(role=actor_role)

    system_prompt = (
        "You are a strict classifier that decides if the user is requesting a database action.\n"
        "Database actions include reading or changing records in database tables.\n"
        "\n"
        f"{schema_description}\n"
        "\n"
        "If the user is just chatting or asking questions without requesting DB access, return false.\n"
        "Examples of DB actions:\n"
        "- 'I am Bilbo Baggins' → update user name\n"
        "- 'How many restaurants do I have?' → read restaurants\n"
        "- 'Add a new outlet' → create restaurant\n"
        "- 'How many employees have I added?' → read restaurant_users\n"
        "- 'Generate an invite code' → create invite_codes\n"
        "\n"
        "Output ONLY valid JSON in this exact schema:\n"
        '{"is_db_action":true|false,"confidence":0.0,"reason":"short"}\n'
    )

    text, data, headers, latency_ms = (
        create_chat_completion_text_allow_empty_with_http_info(
            settings=gate_settings,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"User message:\n{user_text}"},
            ],
            temperature=0.0,
        )
    )

    if text is None:
        return DBIntentResult(False, "empty", None, data, headers, latency_ms, False)

    parsed = _parse_json(text)
    if not isinstance(parsed, dict):
        return DBIntentResult(
            False, "unparseable", None, data, headers, latency_ms, False
        )

    is_db_action = parsed.get("is_db_action")
    confidence = parsed.get("confidence")
    reason = parsed.get("reason")

    is_db_action_bool = bool(is_db_action)
    conf_float = float(confidence) if isinstance(confidence, (int, float)) else None
    reason_str = reason if isinstance(reason, str) else None

    if is_db_action_bool and isinstance(conf_float, float) and conf_float >= 0.7:
        return DBIntentResult(
            True, reason_str, conf_float, data, headers, latency_ms, False
        )
    return DBIntentResult(
        False, reason_str, conf_float, data, headers, latency_ms, False
    )


def extract_db_action(
    *,
    messages: list[TelegramMessages],
    settings: Settings,
    actor_role: str,
    history_messages: list[dict[str, Any]] | None = None,
) -> DBActionResult:
    user_text = _combined_user_text(messages=messages)
    if not user_text:
        return DBActionResult(None, ["no_text"], None, {}, {}, 0)

    # Get comprehensive schema description
    schema_description = get_table_column_descriptions(role=actor_role)

    # Build context from history messages
    context_text = ""
    if history_messages:
        context_parts = []
        for msg in history_messages[-10:]:  # Last 10 messages for context
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                context_parts.append(f"{role}: {content}")
        if context_parts:
            context_text = "\n\nRecent conversation history:\n" + "\n".join(
                context_parts
            )

    system_prompt = (
        "You are extracting a structured DB action from user messages.\n"
        "Use the conversation history to infer missing information (e.g., restaurant names mentioned earlier).\n"
        "Make best-effort decisions based on available context.\n"
        "\n"
        f"{schema_description}\n"
        "\n"
        "Examples:\n"
        "- 'I am Bilbo Baggins' → update users, values={full_name: 'Bilbo Baggins'}, scope=self\n"
        "- 'How many outlets do I have?' → read restaurants, scope=owned_restaurant\n"
        "- 'How many employees have I added?' → read restaurant_users, scope=restaurant_owner\n"
        "- 'Add a new outlet called Pizza Place' → create restaurants, values={name: 'Pizza Place'}, scope=owned_restaurant\n"
        "- 'Generate an invite code for staff' → create invite_codes, values={role: 'staff'}, scope=restaurant_owner\n"
        "\n"
        "For create operations:\n"
        "- If required fields are missing, try to infer from context or use sensible defaults\n"
        "- For invite_codes: auto-generate code if not provided, set expires_at to 30 days from now if not provided\n"
        "- For restaurants: if name not provided, ask but don't block\n"
        "\n"
        "For multiple restaurants:\n"
        "- Use the most recently mentioned restaurant in conversation history\n"
        "- If none mentioned, use the first/primary restaurant\n"
        "\n"
        "Return ONLY valid JSON matching this schema:\n"
        "{\n"
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
        "Set confidence high (>=0.8) if you're certain, medium (0.5-0.8) if inferred from context, low (<0.5) if unsure.\n"
    )

    user_prompt = f"User message:\n{user_text}{context_text}"

    text, data, headers, latency_ms = (
        create_chat_completion_text_allow_empty_with_http_info(
            settings=settings,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
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
