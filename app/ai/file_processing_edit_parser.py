from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ai.model_config import get_presenter_model
from app.ai.openai_client import OpenAIError, create_chat_completion_text_allow_empty_with_http_info
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings


@dataclass(frozen=True)
class FileProcessingEditTelemetry:
    model: str
    latency_ms: int
    generation_id: str | None
    usage: dict[str, Any]


_EDIT_SYSTEM_PROMPT = """You convert user corrections into JSON updates for file-processing data.

Return STRICT JSON only (no markdown, no backticks).

Schema:
{
  "updates": [
    {"field_path": "string", "new_value": any}
  ]
}

Rules:
- Use dot notation for nested fields, 0-based indexes for lists (e.g., line_items.5.description_raw).
- If the user gives a line number (1-based), convert to 0-based index.
- If the user does not specify a field, update the most likely text field (e.g., description/name).
- If no edits are requested, return {"updates": []}.
- Never include IDs or UUIDs in the output.
"""


def _preview_items(
    *,
    processing_type: str,
    extracted_data: dict[str, Any],
    max_items: int,
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    total = 0
    if processing_type == "invoice":
        raw_items = extracted_data.get("line_items") if isinstance(extracted_data.get("line_items"), list) else []
        total = len(raw_items)
        for idx, item in enumerate(raw_items[:max_items], 1):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "index": idx,
                    "description_raw": item.get("description_raw", item.get("description")),
                    "quantity": item.get("quantity"),
                    "unit": item.get("unit"),
                    "unit_price": item.get("unit_price"),
                    "line_total": item.get("line_total"),
                }
            )
    elif processing_type == "price_list":
        raw_items = extracted_data.get("items") if isinstance(extracted_data.get("items"), list) else []
        total = len(raw_items)
        for idx, item in enumerate(raw_items[:max_items], 1):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "index": idx,
                    "supplier_name_raw": item.get("supplier_name_raw", item.get("name")),
                    "price": item.get("price"),
                    "currency": item.get("currency"),
                    "unit_basis": item.get("unit_basis"),
                    "pack_size_text": item.get("pack_size_text"),
                    "min_order_qty": item.get("min_order_qty"),
                }
            )
    elif processing_type == "inventory":
        raw_items = extracted_data.get("items") if isinstance(extracted_data.get("items"), list) else []
        total = len(raw_items)
        for idx, item in enumerate(raw_items[:max_items], 1):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "index": idx,
                    "product_name": item.get("product_name"),
                    "quantity": item.get("quantity"),
                    "unit": item.get("unit"),
                    "unit_cost": item.get("unit_cost"),
                    "status": item.get("status"),
                }
            )
    return items, total


def parse_file_processing_edits(
    *,
    settings: Settings,
    user_message: str,
    processing_type: str,
    extracted_data: dict[str, Any],
    max_preview_items: int = 50,
) -> tuple[list[dict[str, Any]], FileProcessingEditTelemetry | None]:
    model = get_presenter_model(settings)
    parse_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    items_preview, total_items = _preview_items(
        processing_type=processing_type,
        extracted_data=extracted_data,
        max_items=max_preview_items,
    )

    top_level: dict[str, Any] = {}
    if processing_type in {"invoice", "price_list"}:
        top_level["supplier_name"] = extracted_data.get("supplier_name")
        top_level["currency"] = extracted_data.get("currency")
    if processing_type == "invoice":
        top_level["invoice_number"] = extracted_data.get("invoice_number")
        top_level["invoice_date"] = extracted_data.get("invoice_date")
        top_level["due_date"] = extracted_data.get("due_date")
        top_level["total"] = extracted_data.get("total")

    payload = {
        "processing_type": processing_type,
        "user_message": user_message,
        "top_level_fields": top_level,
        "items_preview": items_preview,
        "items_total": total_items,
        "indexing": "User line numbers are 1-based; use 0-based indexes in field_path.",
    }

    try:
        text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
            settings=parse_settings,
            messages=[
                {"role": "system", "content": _EDIT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1,
            extra_body={"max_tokens": 300},
        )
    except OpenAIError:
        return [], None

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    telemetry = FileProcessingEditTelemetry(
        model=str(data.get("model", model)),
        latency_ms=latency_ms,
        generation_id=generation_id,
        usage=usage if isinstance(usage, dict) else {},
    )

    if not isinstance(text, str) or not text.strip():
        return [], telemetry

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [], telemetry

    updates: list[dict[str, Any]] = []
    raw_updates = parsed.get("updates") if isinstance(parsed, dict) else None
    if isinstance(raw_updates, list):
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            field_path = item.get("field_path") or item.get("path")
            if not isinstance(field_path, str) or not field_path.strip():
                continue
            if "new_value" in item:
                new_value = item.get("new_value")
            elif "value" in item:
                new_value = item.get("value")
            else:
                continue
            updates.append({"field_path": field_path.strip(), "new_value": new_value})

    return updates, telemetry
