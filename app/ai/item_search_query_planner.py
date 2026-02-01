from __future__ import annotations

import json
from dataclasses import dataclass

from app.ai.model_config import get_item_search_parse_model
from app.ai.openai_client import (
    OpenAIError,
    create_chat_completion_text_allow_empty_with_http_info,
)
from app.core.config import Settings


@dataclass(frozen=True)
class ItemSearchQueryPlan:
    raw: str
    queries: list[str]
    include_terms: list[str]
    exclude_terms: list[str]
    category_hints: list[str]
    outlet: str | None = None
    supplier: str | None = None
    status: str | None = None
    limit: int | None = None


ITEM_SEARCH_PARSE_SYSTEM_PROMPT = """You extract a structured item-search plan from a Telegram message.

Return STRICT JSON only (no markdown, no backticks, no explanation).

Schema:
{
  "queries": string[],              // 1..3 query variants
  "include_terms": string[],        // 0..5
  "exclude_terms": string[],        // 0..5
  "category_hints": string[],       // 0..5
  "outlet": string|null,            // outlet name if user mentions one (e.g. "for Mercato")
  "supplier": string|null,          // supplier name if user mentions one (e.g. "from Fresh Farms")
  "status": "active"|"inactive"|null,
  "limit": number|null              // 1..10
}

Rules:
- If unsure, set outlet/supplier/status/limit to null.
- Keep queries short and concrete. If unsure, use the user's cleaned query as the only element of queries.
- Hard caps: queries 1..3; include/exclude/category 0..5.
- Never output any IDs or UUIDs.
"""


def _as_str_list(value: object, *, max_len: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        if len(out) >= max_len:
            break
    return out


def _clamp_limit(value: object) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return 1
    if n > 10:
        return 10
    return n


def _normalize_parse_payload(*, raw: str, payload: dict[str, object]) -> ItemSearchQueryPlan:
    queries = _as_str_list(payload.get("queries"), max_len=3)
    if not queries:
        queries = [raw.strip()] if raw.strip() else []
    queries = queries[:3]
    if not queries:
        queries = [""]  # will be handled by caller fallback

    include_terms = _as_str_list(payload.get("include_terms"), max_len=5)
    exclude_terms = _as_str_list(payload.get("exclude_terms"), max_len=5)
    category_hints = _as_str_list(payload.get("category_hints"), max_len=5)

    outlet = payload.get("outlet")
    outlet_s = outlet.strip() if isinstance(outlet, str) else None
    supplier = payload.get("supplier")
    supplier_s = supplier.strip() if isinstance(supplier, str) else None

    status = payload.get("status")
    status_s = status.strip().lower() if isinstance(status, str) else None
    if status_s not in (None, "active", "inactive"):
        status_s = None

    limit = _clamp_limit(payload.get("limit"))

    return ItemSearchQueryPlan(
        raw=raw,
        queries=[q for q in queries if q.strip()][:3] or [raw.strip() or ""],
        include_terms=include_terms,
        exclude_terms=exclude_terms,
        category_hints=category_hints,
        outlet=outlet_s or None,
        supplier=supplier_s or None,
        status=status_s,
        limit=limit,
    )


def plan_item_search(
    *,
    semantic_text: str,
    settings: Settings,
) -> tuple[ItemSearchQueryPlan, dict[str, object] | None, dict[str, str], int]:
    """Plan an item search with a single cheap JSON-only LLM call.

    Returns:
      (plan, raw_openai_data, raw_headers, latency_ms)
    """
    model = get_item_search_parse_model(settings)
    parse_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )
    parse_settings = parse_settings.model_copy(
        update={"openai_timeout_seconds": min(parse_settings.openai_timeout_seconds, 6.0)}
    )

    raw = semantic_text.strip()
    if not raw:
        return (
            ItemSearchQueryPlan(
                raw="",
                queries=[""],
                include_terms=[],
                exclude_terms=[],
                category_hints=[],
                outlet=None,
                supplier=None,
                status=None,
                limit=None,
            ),
            None,
            {},
            0,
        )

    try:
        text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
            settings=parse_settings,
            messages=[
                {"role": "system", "content": ITEM_SEARCH_PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": raw},
            ],
            temperature=0.0,
            extra_body={"max_tokens": 200},
        )
    except OpenAIError:
        return (
            ItemSearchQueryPlan(
                raw=raw,
                queries=[raw],
                include_terms=[],
                exclude_terms=[],
                category_hints=[],
            ),
            None,
            {},
            0,
        )

    payload: dict[str, object] = {}
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    return _normalize_parse_payload(raw=raw, payload=payload), data, headers, latency_ms

