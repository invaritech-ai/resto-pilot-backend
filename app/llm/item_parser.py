"""
Item parser — LLM-based structured extraction for invoices and price lists.

Uses the OpenAI Python client (works with OpenAI native, OpenRouter, or any
OpenAI-compatible endpoint) configured via app.core.config vision_* settings.

Public API:
    parse_invoice(settings, *, text=None, image_b64=None, image_mime=...) → dict
    parse_price_list(settings, *, text=None, image_b64=None, image_mime=...) → dict
    ParseError — raised when extraction fails validation

Output schema:
    Invoice:
        supplier, supplier_contact_name, supplier_phone, supplier_email,
        invoice_date, invoice_number, currency,
        line_items: [{name, qty, unit, unit_price, amount}]

    Price list:
        supplier, supplier_contact_name, supplier_phone, supplier_email,
        lead_time, effective_date, currency,
        line_items: [{name, unit, unit_price}]

All top-level string fields are nullable. line_items is always a list.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when LLM output cannot be parsed or validated."""
    pass


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_INVOICE_SYSTEM = """\
You are an expert at extracting structured data from supplier invoices.

Return a single JSON object (no markdown, no code fences):
{
  "supplier": "supplier company name or null",
  "supplier_contact_name": "contact name or null",
  "supplier_phone": "phone or null",
  "supplier_email": "email or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "invoice ref or null",
  "currency": "ISO 4217 code e.g. USD, HKD or null",
  "line_items": [
    {"name": "item name", "qty": 1.0, "unit": "kg or null", "unit_price": 10.00, "amount": 10.00}
  ]
}

Rules:
- Extract EVERY line item. qty and unit_price must be positive numbers.
- amount is optional (qty × unit_price). Use null if missing or unclear.
- Missing values → null. Do NOT invent values.
- Return ONLY the JSON object.
"""

_PRICE_LIST_SYSTEM = """\
You are an expert at extracting structured data from supplier price lists.

Return a single JSON object (no markdown, no code fences):
{
  "supplier": "supplier company name or null",
  "supplier_contact_name": "contact name or null",
  "supplier_phone": "phone or null",
  "supplier_email": "email or null",
  "lead_time": "e.g. 3-5 business days or null",
  "effective_date": "YYYY-MM-DD or null",
  "currency": "ISO 4217 code e.g. USD, HKD or null",
  "line_items": [
    {"name": "item name", "unit": "kg or null", "unit_price": 10.00}
  ]
}

Rules:
- Extract EVERY product. unit_price must be a positive number (no currency symbols).
- Missing values → null. Do NOT invent values.
- Return ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_invoice(
    settings: Settings,
    *,
    text: str | None = None,
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
) -> dict:
    """Extract structured invoice data from text or image.

    Exactly one of `text` or `image_b64` must be provided.

    Returns:
        Dict with keys: supplier, supplier_contact_name, supplier_phone,
        supplier_email, invoice_date, invoice_number, currency, line_items.

    Raises:
        ValueError:  Neither or both inputs provided.
        ParseError:  LLM returned bad JSON or missing required fields.
    """
    return _call_llm(
        system_prompt=_INVOICE_SYSTEM,
        document_type="invoice",
        settings=settings,
        text=text,
        image_b64=image_b64,
        image_mime=image_mime,
    )


def parse_price_list(
    settings: Settings,
    *,
    text: str | None = None,
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
) -> dict:
    """Extract structured price list data from text or image.

    Exactly one of `text` or `image_b64` must be provided.

    Returns:
        Dict with keys: supplier, supplier_contact_name, supplier_phone,
        supplier_email, lead_time, effective_date, currency, line_items.

    Raises:
        ValueError:  Neither or both inputs provided.
        ParseError:  LLM returned bad JSON or missing required fields.
    """
    return _call_llm(
        system_prompt=_PRICE_LIST_SYSTEM,
        document_type="price_list",
        settings=settings,
        text=text,
        image_b64=image_b64,
        image_mime=image_mime,
    )


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _call_llm(
    system_prompt: str,
    document_type: str,
    settings: Settings,
    *,
    text: str | None,
    image_b64: str | None,
    image_mime: str,
) -> dict:
    """Shared LLM call logic for both document types."""
    if text is None and image_b64 is None:
        raise ValueError("Provide either text or image_b64")
    if text is not None and image_b64 is not None:
        raise ValueError("Provide either text or image_b64, not both")

    try:
        from openai import OpenAI
    except ImportError as e:
        raise ParseError("openai package not installed") from e

    client = OpenAI(
        api_key=settings.vision_api_key or settings.openai_api_key,
        base_url=settings.vision_base_url or settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    model = settings.vision_model or settings.openai_model

    if image_b64 is not None:
        content: Any = [
            {"type": "text", "text": system_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            },
        ]
    else:
        content = f"{system_prompt}\n\nDOCUMENT CONTENT:\n{text}"

    logger.info(
        "item_parser_call doc_type=%s model=%s input=%s",
        document_type,
        model,
        "image" if image_b64 else "text",
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
        )
    except Exception as exc:
        logger.error("item_parser_llm_error doc_type=%s error=%s", document_type, exc)
        raise ParseError(f"LLM call failed: {exc}") from exc

    raw_text = (response.choices[0].message.content or "").strip()

    # Strip markdown fences if model included them
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("item_parser_json_error doc_type=%s raw=%s", document_type, raw_text[:300])
        raise ParseError(f"LLM returned invalid JSON: {exc}") from exc

    return _validate(parsed, document_type)


def _validate(raw: dict, document_type: str) -> dict:
    """Validate and normalise the LLM output."""
    if not isinstance(raw, dict):
        raise ParseError(f"Expected dict from LLM, got {type(raw).__name__}")

    result: dict = {}

    # Top-level string fields (all nullable)
    string_fields = (
        "supplier", "supplier_contact_name", "supplier_phone", "supplier_email",
        "currency", "invoice_date", "invoice_number", "effective_date", "lead_time",
    )
    for field in string_fields:
        val = raw.get(field)
        result[field] = str(val).strip() or None if val is not None else None

    # line_items
    raw_items = raw.get("line_items")
    if raw_items is None:
        raise ParseError("LLM output missing 'line_items'")
    if not isinstance(raw_items, list):
        raise ParseError(f"'line_items' must be a list, got {type(raw_items).__name__}")

    line_items = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            continue

        unit_price = _to_float(item.get("unit_price"))
        if unit_price is None or unit_price <= 0:
            logger.warning("item_parser: skip item %r — bad unit_price", name)
            continue

        row: dict = {
            "name": name,
            "unit": str(item["unit"]).strip() if item.get("unit") else None,
            "unit_price": unit_price,
        }

        if document_type == "invoice":
            qty = _to_float(item.get("qty"))
            if qty is None or qty <= 0:
                logger.warning("item_parser: skip invoice item %r — bad qty", name)
                continue
            row["qty"] = qty
            amount = _to_float(item.get("amount"))
            row["amount"] = amount if amount and amount > 0 else None

        line_items.append(row)

    result["line_items"] = line_items
    return result


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
