"""
Item parser — two-stage LLM pipeline for invoices and price lists.

Stage 1 (ocr_page_to_markdown):
    Vision LLM reads the image and returns clean Markdown text.
    Uses vision_* settings (e.g. Gemini Flash).

Stage 2 (parse_invoice / parse_price_list):
    Cheap text LLM reads the Markdown and returns structured JSON.
    Uses parser_* settings (e.g. gpt-4o-mini), falling back to openai_* if blank.

Public API:
    ocr_page_to_markdown(settings, image_b64, image_mime) → str
    parse_invoice(settings, text) → dict
    parse_price_list(settings, text) → dict
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

from collections.abc import Callable
import json
import logging

from openai import OpenAI

from app.core.config import Settings

logger = logging.getLogger(__name__)

LlmCallCallback = Callable[[dict[str, object]], None]


class ParseError(Exception):
    """Raised when LLM output cannot be parsed or validated."""
    pass


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

OCR_PROMPT = (
    "Extract all text from this page (1/1). "
    "This may contain handwritten text - carefully distinguish similar-looking "
    "characters (4 vs 9, 1 vs 7, 0 vs 6, 3 vs 8, 5 vs S). "
    "Preserve tables using Markdown table syntax. "
    "Keep all headers, footers, and page numbers. "
    "Use headings (##, ###) for section titles. "
    "Return ONLY Markdown - no commentary."
)

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

def ocr_page_to_markdown(
    settings: Settings,
    image_b64: str,
    image_mime: str = "image/jpeg",
    on_llm_call: LlmCallCallback | None = None,
) -> str:
    """Stage 1: send image to vision LLM, returns raw Markdown text.

    Uses vision_* settings (vision_model, vision_api_key, vision_base_url).

    Raises:
        ParseError: LLM call failed.
    """
    client = OpenAI(
        api_key=settings.vision_api_key or settings.openai_api_key,
        base_url=settings.vision_base_url or settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    model = settings.vision_model or settings.openai_model

    logger.info("ocr_page_to_markdown model=%s", model)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                    },
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }],
            temperature=0.0,
        )
    except Exception as exc:
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": "ocr_page_to_markdown",
                "model": model,
                "error": str(exc),
            },
        )
        logger.error("ocr_page_to_markdown error: %s", exc)
        raise ParseError(f"OCR LLM call failed: {exc}") from exc

    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": "ocr_page_to_markdown",
            "model": model,
            "upstream_id": getattr(response, "id", None),
            "usage": _extract_usage_dict(response),
        },
    )
    return (response.choices[0].message.content or "").strip()


def parse_invoice(
    settings: Settings,
    text: str,
    on_llm_call: LlmCallCallback | None = None,
) -> dict:
    """Stage 2: extract structured invoice data from Markdown text.

    Returns:
        Dict with keys: supplier, supplier_contact_name, supplier_phone,
        supplier_email, invoice_date, invoice_number, currency, line_items.

    Raises:
        ParseError: LLM returned bad JSON or missing required fields.
    """
    return _call_parser(
        system_prompt=_INVOICE_SYSTEM,
        document_type="invoice",
        settings=settings,
        text=text,
        on_llm_call=on_llm_call,
    )


def parse_price_list(
    settings: Settings,
    text: str,
    on_llm_call: LlmCallCallback | None = None,
) -> dict:
    """Stage 2: extract structured price list data from Markdown text.

    Returns:
        Dict with keys: supplier, supplier_contact_name, supplier_phone,
        supplier_email, lead_time, effective_date, currency, line_items.

    Raises:
        ParseError: LLM returned bad JSON or missing required fields.
    """
    return _call_parser(
        system_prompt=_PRICE_LIST_SYSTEM,
        document_type="price_list",
        settings=settings,
        text=text,
        on_llm_call=on_llm_call,
    )


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _call_parser(
    system_prompt: str,
    document_type: str,
    settings: Settings,
    text: str,
    on_llm_call: LlmCallCallback | None = None,
) -> dict:
    """Shared stage-2 LLM call: Markdown text → structured JSON dict."""
    client = OpenAI(
        api_key=settings.parser_api_key or settings.openai_api_key,
        base_url=settings.parser_base_url or settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    model = settings.parser_model or settings.openai_model

    logger.info(
        "item_parser_call doc_type=%s model=%s",
        document_type,
        model,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"DOCUMENT CONTENT:\n{text}"},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": f"parse_{document_type}",
                "model": model,
                "error": str(exc),
            },
        )
        logger.error("item_parser_llm_error doc_type=%s error=%s", document_type, exc)
        raise ParseError(f"LLM call failed: {exc}") from exc

    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": f"parse_{document_type}",
            "model": model,
            "upstream_id": getattr(response, "id", None),
            "usage": _extract_usage_dict(response),
        },
    )

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


def _extract_usage_dict(response: object) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    usage_dict: dict[str, int] = {}
    if isinstance(prompt_tokens, int):
        usage_dict["prompt_tokens"] = prompt_tokens
    if isinstance(completion_tokens, int):
        usage_dict["completion_tokens"] = completion_tokens
    if isinstance(total_tokens, int):
        usage_dict["total_tokens"] = total_tokens

    return usage_dict or None


def _emit_llm_call(
    *,
    on_llm_call: LlmCallCallback | None,
    payload: dict[str, object],
) -> None:
    if on_llm_call is None:
        return
    try:
        on_llm_call(payload)
    except Exception:
        logger.exception("item_parser_on_llm_call_failed")


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
        unit = str(item["unit"]).strip() if item.get("unit") else None

        if document_type == "invoice":
            qty = _to_float(item.get("qty"))
            if qty is not None and qty <= 0:
                qty = None  # treat zero/negative as missing
            if qty is None:
                logger.warning("item_parser: qty missing for %r — including with null for user review", name)
            amount = _to_float(item.get("amount"))

            # Derive unit_price from amount/qty if not directly provided
            if unit_price is None or unit_price <= 0:
                if amount and amount > 0 and qty is not None and qty > 0:
                    unit_price = round(amount / qty, 6)
                    logger.info(
                        "item_parser: derived unit_price for %r: amount=%s qty=%s → %s",
                        name, amount, qty, unit_price,
                    )
                else:
                    logger.warning("item_parser: unit_price missing for %r — including with null for user review", name)
                    unit_price = None

            row: dict = {
                "name": name,
                "unit": unit,
                "unit_price": unit_price,
                "qty": qty,
                "amount": amount if amount and amount > 0 else None,
            }

        else:
            if unit_price is None or unit_price <= 0:
                logger.warning("item_parser: unit_price missing for %r — including with null for user review", name)
                unit_price = None
            row = {
                "name": name,
                "unit": unit,
                "unit_price": unit_price,
            }

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
