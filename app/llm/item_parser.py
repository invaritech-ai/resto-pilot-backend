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
import time

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

    t0 = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_mime};base64,{image_b64}"
                            },
                        },
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }
            ],
            temperature=0.0,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": "ocr_page_to_markdown",
                "model": model,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        logger.error("ocr_page_to_markdown error: %s", exc)
        raise ParseError(f"OCR LLM call failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": "ocr_page_to_markdown",
            "model": model,
            "latency_ms": latency_ms,
            **_extract_response_meta(response),
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


# _NL_QUERY_SYSTEM = """\
# You are a concise, practical restaurant ops assistant.
# Help staff check inventory, suppliers, and stock — no small talk.

# The user has sent a message. Classify it:
# - route_command: user wants a list/view that maps to an existing bot command
# - answer: user is asking a factual question you can answer from the CONTEXT below
# - unknown: cannot be interpreted as a restaurant data query

# Routable commands:
#   /list suppliers   — all linked suppliers
#   /products         — supplier product catalog
#   /inventory        — stock levels (all items)
#   /balance          — stock summary (totals, zero-stock, negative)
#   /uploads          — pending upload reviews

# Return ONLY valid JSON — no markdown, no explanation:
# {{
#   "action": "route_command" | "answer" | "unknown",
#   "command": "/inventory" | "/products" | "/balance" | "/list suppliers" | "/uploads" (only when action=route_command),
#   "answer": "your response" (only when action=answer)
# }}

# Rules for "answer":
# - Use ONLY the data in CONTEXT. Never invent numbers, names, or prices.
# - If context data is missing/zero, say so and suggest the relevant command.
# - Keep answers under 3 sentences.
# - If unsure between route_command and answer, prefer route_command.

# CONTEXT:
# {context}
# """

_NL_QUERY_SYSTEM = """\
You are a cheerful anime-style Japanese chef assistant (adult), quick, upbeat, and concise.
Tone: playful kitchen energy, friendly, no romance, no flirting, no roleplay beyond light style.

You are READ-ONLY.
You must never assist with write/mutate actions (add, edit, delete, approve, cancel, upload, \
confirm, link, unlink, adjust stock).
If user requests a write action, return:
{{
  "action": "answer",
  "answer": "I can only help with read-only queries right now. Try /inventory, /products, /balance, /list suppliers, or /uploads."
}}

Classify user input as:
- route_command (read-only list/view commands only)
- answer (factual answer from context only)
- unknown

Allowed route commands:
  /list suppliers       — all linked suppliers
  /products             — supplier product catalog
  /inventory            — stock levels (all items)
  /balance              — stock summary (totals, zero-stock, negative)
  /uploads              — pending upload reviews
  /search <terms>       — unified search across inventory, products & suppliers
  /par                  — view par levels (minimum stock thresholds) vs current stock
  /reorder              — smart reorder list: items below par with best price and supplier
  /orders               — view open purchase orders
  /spend                — spend analytics by supplier for current month

Return ONLY valid JSON — no markdown, no explanation:
{{
  "action": "route_command" | "answer" | "unknown",
  "command": "/inventory" | "/products" | "/balance" | "/list suppliers" | "/uploads" | \
"/search <terms>" | "/par" | "/reorder" | "/orders" | "/spend" \
(only when action=route_command),
  "answer": "your response" (only when action=answer)
}}

Rules for "route_command":
- Use /search <terms> for queries about specific ingredients, products, or suppliers by name.
  Examples: "where can I buy truffle?" → /search truffle | "do we have chicken?" → /search chicken
- Use /reorder when user asks what to reorder, is running low, or needs to restock.
  Examples: "what do I need to order?" → /reorder | "my chicken is running low" → /reorder
- Use /orders when user asks about pending orders, order status, or what's been ordered.
  Examples: "show me my open orders" → /orders | "do I have any pending orders?" → /orders
- Use /par when user asks about par levels or minimum stock thresholds.
  Examples: "what's my par level for onion?" → /par | "show my minimum stock levels" → /par
- Use /spend when user asks about spending, supplier costs, bills, or how much was spent.
  Examples: "how much did I spend last month?" → /spend | "what are my supplier costs?" → /spend

Rules for "answer":
- Use ONLY the data in CONTEXT. Never invent numbers, names, or prices.
- If context data is missing/zero, say so and suggest /search <relevant term>.
- Keep answers under 3 sentences.
- If unsure between route_command and answer, prefer route_command.

CONTEXT:
{context}
"""


_STOCK_ADJUSTMENT_SYSTEM = (
    "You parse natural language inventory adjustment messages from restaurant staff.\n"
    "Extract and return ONLY valid JSON with these fields:\n"
    "  item_name  (string)  — the ingredient or product name\n"
    "  quantity   (number)  — a positive number (always positive, direction handled separately)\n"
    "  unit       (string|null) — unit like kg, g, L, pkt, box, etc.; null if not mentioned\n"
    "  direction  (string|null) — 'in' if received/added/left on hand, 'out' if used/consumed/sold/removed; null if ambiguous\n"
    'If the message is NOT an inventory adjustment, return: {"error": "not_stock_adjustment"}\n'
    "Examples:\n"
    '  \'got 5kg chicken\' → {"item_name":"chicken","quantity":5,"unit":"kg","direction":"in"}\n'
    '  \'used 2.5 kg beef\' → {"item_name":"beef","quantity":2.5,"unit":"kg","direction":"out"}\n'
    '  \'+3 boxes milk\' → {"item_name":"milk","quantity":3,"unit":"boxes","direction":"in"}\n'
    '  \'-500g butter\' → {"item_name":"butter","quantity":500,"unit":"g","direction":"out"}\n'
    '  \'onion 1kg\' → {"item_name":"onion","quantity":1,"unit":"kg","direction":null}\n'
    '  \'1kg onion left\' → {"item_name":"onion","quantity":1,"unit":"kg","direction":"in"}\n'
    '  \'hello\' → {"error":"not_stock_adjustment"}\n'
    "Return ONLY the JSON object, no markdown, no explanation."
)


def parse_stock_adjustment(
    settings: Settings,
    text: str,
    on_llm_call: LlmCallCallback | None = None,
) -> dict:
    """Parse natural language inventory adjustment into structured data.

    Returns:
        Dict with keys: item_name (str), quantity (float), unit (str|None),
        direction ("in"|"out"|None).

    Raises:
        ParseError: text is not a stock adjustment or LLM returned bad output.
    """
    client = OpenAI(
        api_key=settings.chat_api_key or settings.openai_api_key,
        base_url=settings.chat_base_url or settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    model = settings.chat_model or settings.openai_model

    t0 = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _STOCK_ADJUSTMENT_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": "stock_adjustment",
                "model": model,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        raise ParseError(f"LLM call failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": "stock_adjustment",
            "model": model,
            "latency_ms": latency_ms,
            **_extract_response_meta(response),
        },
    )

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        ).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ParseError("LLM returned non-dict")
    if "error" in parsed:
        raise ParseError(f"Not a stock adjustment: {parsed['error']}")

    item_name = str(parsed.get("item_name") or "").strip()
    if not item_name:
        raise ParseError("Missing item_name in stock adjustment")
    quantity = _to_float(parsed.get("quantity"))
    if quantity is None or quantity <= 0:
        raise ParseError(f"Invalid quantity: {parsed.get('quantity')!r}")
    unit_raw = parsed.get("unit")
    unit = str(unit_raw).strip() if unit_raw else None
    direction_raw = parsed.get("direction")
    direction = str(direction_raw) if direction_raw in ("in", "out") else None

    return {
        "item_name": item_name,
        "quantity": quantity,
        "unit": unit,
        "direction": direction,
    }


def classify_and_answer(
    settings: Settings,
    text: str,
    context_snippet: str,
    on_llm_call: LlmCallCallback | None = None,
) -> dict:
    """Classify user intent and optionally answer from injected context.

    Returns:
        Dict with key "action" ("route_command", "answer", or "unknown").
        When action="route_command": also has "command" (str).
        When action="answer": also has "answer" (str).

    Raises:
        ParseError: LLM call failed or returned unparseable JSON.
        Callers treat ParseError as action="unknown" and fall through.
    """
    client = OpenAI(
        api_key=settings.chat_api_key or settings.openai_api_key,
        base_url=settings.chat_base_url or settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    model = settings.chat_model or settings.openai_model

    system_prompt = _NL_QUERY_SYSTEM.format(context=context_snippet)

    t0 = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": "nl_query",
                "model": model,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        raise ParseError(f"LLM call failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": "nl_query",
            "model": model,
            "latency_ms": latency_ms,
            **_extract_response_meta(response),
        },
    )

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        ).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ParseError("LLM returned non-dict")

    action = parsed.get("action")
    if action not in ("route_command", "answer", "unknown"):
        raise ParseError(f"Unexpected action value: {action!r}")

    return parsed


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

    t0 = time.monotonic()
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
        latency_ms = int((time.monotonic() - t0) * 1000)
        _emit_llm_call(
            on_llm_call=on_llm_call,
            payload={
                "purpose": f"parse_{document_type}",
                "model": model,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        logger.error("item_parser_llm_error doc_type=%s error=%s", document_type, exc)
        raise ParseError(f"LLM call failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    _emit_llm_call(
        on_llm_call=on_llm_call,
        payload={
            "purpose": f"parse_{document_type}",
            "model": model,
            "latency_ms": latency_ms,
            **_extract_response_meta(response),
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
        logger.error(
            "item_parser_json_error doc_type=%s raw=%s", document_type, raw_text[:300]
        )
        raise ParseError(f"LLM returned invalid JSON: {exc}") from exc

    return _validate(parsed, document_type)


def _extract_response_meta(response: object) -> dict[str, object]:
    """Extract all telemetry fields from a chat completion response.

    Handles OpenRouter-specific extensions (cost, generation ID) and
    falls back gracefully for standard OpenAI responses.

    Returns a dict suitable for spreading into an _emit_llm_call payload.
    The dict always has keys:
        openrouter_generation_id, upstream_id, usage,
        total_cost_usd, upstream_inference_cost_usd
    """
    response_id: str | None = None
    raw_id = getattr(response, "id", None)
    if raw_id is not None:
        response_id = str(raw_id)

    # OpenRouter generation IDs start with "gen-"; everything else is a provider ID.
    openrouter_generation_id: str | None = None
    upstream_id: str | None = None
    if response_id:
        if response_id.startswith("gen-"):
            openrouter_generation_id = response_id
        else:
            upstream_id = response_id

    # Token counts (standard across all providers)
    usage_obj = getattr(response, "usage", None)
    usage_dict: dict[str, int] | None = None
    total_cost_usd: float | None = None
    upstream_inference_cost_usd: float | None = None

    if usage_obj is not None:
        tokens: dict[str, int] = {}
        pt = getattr(usage_obj, "prompt_tokens", None)
        ct = getattr(usage_obj, "completion_tokens", None)
        tt = getattr(usage_obj, "total_tokens", None)
        if isinstance(pt, int):
            tokens["prompt_tokens"] = pt
        if isinstance(ct, int):
            tokens["completion_tokens"] = ct
        if isinstance(tt, int):
            tokens["total_tokens"] = tt
        if tokens:
            usage_dict = tokens

        # OpenRouter-specific: cost in USD, included in every response
        cost_raw = getattr(usage_obj, "cost", None)
        if cost_raw is not None:
            try:
                total_cost_usd = float(cost_raw)
            except (TypeError, ValueError):
                pass

        # OpenRouter-specific: upstream provider cost (inside cost_details)
        cost_details = getattr(usage_obj, "cost_details", None)
        if cost_details is not None:
            upstream_raw = getattr(cost_details, "upstream_inference_cost", None)
            if upstream_raw is not None:
                try:
                    upstream_inference_cost_usd = float(upstream_raw)
                except (TypeError, ValueError):
                    pass

    return {
        "openrouter_generation_id": openrouter_generation_id,
        "upstream_id": upstream_id,
        "usage": usage_dict,
        "total_cost_usd": total_cost_usd,
        "upstream_inference_cost_usd": upstream_inference_cost_usd,
    }


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
        "supplier",
        "supplier_contact_name",
        "supplier_phone",
        "supplier_email",
        "currency",
        "invoice_date",
        "invoice_number",
        "effective_date",
        "lead_time",
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
                logger.warning(
                    "item_parser: qty missing for %r — including with null for user review",
                    name,
                )
            amount = _to_float(item.get("amount"))

            # Derive unit_price from amount/qty if not directly provided
            if unit_price is None or unit_price <= 0:
                if amount and amount > 0 and qty is not None and qty > 0:
                    unit_price = round(amount / qty, 6)
                    logger.info(
                        "item_parser: derived unit_price for %r: amount=%s qty=%s → %s",
                        name,
                        amount,
                        qty,
                        unit_price,
                    )
                else:
                    logger.warning(
                        "item_parser: unit_price missing for %r — including with null for user review",
                        name,
                    )
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
                logger.warning(
                    "item_parser: unit_price missing for %r — including with null for user review",
                    name,
                )
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
