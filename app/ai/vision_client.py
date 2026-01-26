from __future__ import annotations

import base64
import io
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.openai_client import (
    OpenAIError,
    _apply_reasoning_policy,
    _is_retryable_exception,
    _is_retryable_status,
    _should_control_reasoning,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class VisionCallResult:
    """Result from a vision model call with telemetry data."""

    content: str
    model: str
    latency_ms: int
    usage: dict[str, int] | None
    openrouter_generation_id: str | None
    response_headers: dict[str, str]
    response_data: dict[str, Any]
    error: str | None = None
    prompt_text: str | None = None
    input_text: str | None = None
    request_payload: dict[str, Any] | None = None


@dataclass
class VisionDocumentResult:
    """Result from processing a document with telemetry data."""

    content: str
    telemetry_results: list[VisionCallResult]


def _log_combined_text(text: str, max_chars: int = 20000) -> None:
    if not text:
        logger.info("vision_combined_text_empty")
        return
    if len(text) <= max_chars:
        snippet = text
        truncated = 0
    else:
        snippet = text[:max_chars]
        truncated = len(text) - max_chars
        snippet += f"\n... [truncated {truncated} chars]"
    logger.info(
        "vision_combined_text",
        extra={"length": len(text), "truncated": truncated, "text": snippet},
    )


def _log_structured_text(text: str, max_chars: int = 20000) -> None:
    if not text:
        logger.info("vision_structured_text_empty")
        return
    if len(text) <= max_chars:
        snippet = text
        truncated = 0
    else:
        snippet = text[:max_chars]
        truncated = len(text) - max_chars
        snippet += f"\n... [truncated {truncated} chars]"
    logger.info(
        "vision_structured_text length=%s truncated=%s\nSTRUCTURED:\n%s",
        len(text),
        truncated,
        snippet,
    )


def _clean_structured_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    elif cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    return cleaned


def _parse_structured_json(text: str) -> dict[str, Any]:
    cleaned = _clean_structured_text(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.exception(
            "vision_structured_json_parse_failed",
            extra={"error": str(exc)},
        )
        raise OpenAIError(f"Failed to parse structured JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenAIError("Structured JSON must be an object")
    return data


def _merge_non_null_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if value is None:
            continue
        if key not in target or target[key] in ("", None, []):
            target[key] = value


def _merge_structured_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        raise OpenAIError("No structured results to merge")

    merged: dict[str, Any] = {}
    for result in results:
        if not merged:
            merged = result.copy()
            continue

        if isinstance(result.get("supplier"), dict):
            supplier = merged.get("supplier")
            if not isinstance(supplier, dict):
                supplier = {}
            _merge_non_null_fields(supplier, result.get("supplier", {}))
            merged["supplier"] = supplier

        for key, value in result.items():
            if key in ("supplier", "line_items", "items", "product_info"):
                continue
            if key not in merged or merged[key] in ("", None, []):
                merged[key] = value

        for key in ("line_items", "items", "product_info"):
            if key in result:
                existing = merged.get(key)
                if not isinstance(existing, list):
                    existing = []
                incoming = result.get(key)
                if isinstance(incoming, list):
                    existing.extend(incoming)
                merged[key] = existing

    if isinstance(merged.get("line_items"), list):
        deduplicated = []
        seen = set()
        for item in merged["line_items"]:
            if not isinstance(item, dict):
                continue
            desc = item.get("description_raw", item.get("description", ""))
            qty = item.get("quantity")
            unit = item.get("unit", "")
            price = item.get("unit_price")
            dedup_key = (desc, qty, unit, price)
            if dedup_key not in seen:
                seen.add(dedup_key)
                deduplicated.append(item)
        merged["line_items"] = deduplicated

    if isinstance(merged.get("items"), list):
        deduplicated = []
        seen = set()
        for item in merged["items"]:
            if not isinstance(item, dict):
                continue
            name = item.get("supplier_name_raw", item.get("name", ""))
            sku = item.get("supplier_sku", "")
            pack_size = item.get("pack_size_text", "")
            unit_basis = item.get("unit_basis", "")
            min_order_qty = item.get("min_order_qty")
            price = item.get("price")
            currency = item.get("currency", "")
            price_type = item.get("price_type", "")
            min_qty = item.get("min_qty")
            valid_from = item.get("valid_from")
            valid_to = item.get("valid_to")
            dedup_key = (
                name,
                sku,
                pack_size,
                unit_basis,
                min_order_qty,
                price,
                currency,
                price_type,
                min_qty,
                valid_from,
                valid_to,
            )
            if dedup_key not in seen:
                seen.add(dedup_key)
                deduplicated.append(item)
        merged["items"] = deduplicated

    return merged


def _log_page_text(
    page_num: int,
    text_layer: str,
    ocr_layer: str,
    max_chars: int = 20000,
) -> None:
    def _clip(value: str) -> tuple[str, int, int]:
        if not value or not value.strip():
            return "[EMPTY]", 0, 0
        length = len(value)
        if length <= max_chars:
            return value, length, 0
        snippet = value[:max_chars] + f"\n... [truncated {length - max_chars} chars]"
        return snippet, length, length - max_chars

    text_snippet, text_length, text_truncated = _clip(text_layer)
    ocr_snippet, ocr_length, ocr_truncated = _clip(ocr_layer)
    logger.info(
        "vision_page_text page=%s text_length=%s text_truncated=%s ocr_length=%s "
        "ocr_truncated=%s\nTEXT:\n%s\nOCR:\n%s",
        page_num,
        text_length,
        text_truncated,
        ocr_length,
        ocr_truncated,
        text_snippet,
        ocr_snippet,
    )


def _get_vision_settings(settings: Settings) -> tuple[str, str, str]:
    """Get vision model settings, falling back to OpenAI defaults if not configured."""
    model = settings.vision_model or settings.openai_model
    api_key = settings.vision_api_key or settings.openai_api_key
    base_url = settings.vision_base_url or settings.openai_base_url

    print(
        f"[VISION] _get_vision_settings: model={model}, base_url={base_url}, api_key={'***' + api_key[-4:] if api_key else 'NONE'}"
    )

    if not api_key:
        raise OpenAIError(
            "Vision API key is not configured (APP_VISION_API_KEY or APP_OPENAI_API_KEY)"
        )

    return model, api_key, base_url


def _get_text_settings(settings: Settings) -> tuple[str, str, str]:
    """
    Get text model settings for extraction (cheaper than vision).

    Uses APP_OPENAI_* config (model, api_key, base_url). Model defaults to
    openai_response_model (if set) to keep extraction fast/cheap.
    """
    model = (settings.openai_response_model or settings.openai_model).strip()
    api_key = settings.openai_api_key
    base_url = settings.openai_base_url

    if not api_key:
        raise OpenAIError(
            "OpenAI API key is not configured (APP_OPENAI_API_KEY)"
        )

    return model, api_key, base_url


def ocr_page_to_markdown(
    *,
    page_image_bytes: bytes,
    page_num: int,
    total_pages: int,
    settings: Settings,
    mime_type: str | None = None,
) -> VisionCallResult:
    """
    OCR a single page and return Markdown output.

    Optimized prompt for pure OCR:
    - Preserve tables, headers, footers
    - Use Markdown formatting
    - Don't summarize or interpret

    Args:
        page_image_bytes: Image bytes for the page
        page_num: Page number (for logging)
        total_pages: Total number of pages (for logging)
        settings: Application settings

    Returns:
        VisionCallResult with Markdown content
    """
    prompt = (
        f"Extract all text from this page ({page_num}/{total_pages}). "
        "Preserve tables using Markdown table syntax. "
        "Keep all headers, footers, and page numbers. "
        "Use headings (##, ###) for section titles. "
        "Return ONLY Markdown - no commentary."
    )
    return process_image_with_vision(
        page_image_bytes, prompt, settings, mime_type or "image/png"
    )


def extract_json_from_markdown(
    *,
    markdown_text: str,
    page_num: int,
    processing_type: str,
    db_schema: str,
    settings: Settings,
    repair_hint: str | None = None,
) -> VisionCallResult:
    """
    Extract structured JSON from Markdown text for a single page.

    Uses text LLM (not vision) with schema-aware prompt.
    Returns JSON aligned with database columns.

    Args:
        markdown_text: Markdown text from OCR
        page_num: Page number (for context)
        processing_type: "invoice", "price_list", or "inventory"
        db_schema: Database schema text for extraction guidance
        settings: Application settings
        repair_hint: Optional repair instructions for invalid JSON retries

    Returns:
        VisionCallResult with JSON content
    """
    repair_block = f"\nRepair instructions:\n{repair_hint}\n" if repair_hint else ""

    if processing_type == "invoice":
        prompt = f"""Extract invoice data from this page ({page_num}) and return ONLY valid JSON.

Schema reference:
{db_schema}
{repair_block}

Output schema:
{{
  "supplier_name": "string",
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD|null",
  "currency": "string",
  "line_items": [
    {{
      "description_raw": "string",
      "quantity": "number",
      "unit": "string",
      "unit_price": "number",
      "line_total": "number",
      "currency": "string",
      "tax_amount": "number",
      "source_page": "number",
      "row_index": "number|null",
      "raw_row": "string|null"
    }}
  ],
  "subtotal": "number|null",
  "tax": "number|null",
  "total": "number|null"
}}

Rules:
1. Use null for missing fields; do not guess.
2. Use the exact field names shown above.
3. description_raw should capture the full line description including specs like pack size or origin.
4. All numeric fields must be numbers, not strings.
5. For each line_item, set source_page={page_num} and include row_index/raw_row when possible.
6. If the page has no relevant data, return {{"line_items": []}}.

Page content (Markdown):
{markdown_text}
"""
    elif processing_type == "price_list":
        prompt = f"""Extract supplier price list data from this page ({page_num}) and return ONLY valid JSON.

Schema reference:
{db_schema}
{repair_block}

Output schema:
{{
  "supplier_name": "string",
  "contact_name": "string|null",
  "contact_email": "string|null",
  "contact_phone": "string|null",
  "currency": "string|null",
  "effective_date": "YYYY-MM-DD|null",
  "items": [
    {{
      "supplier_name_raw": "string",
      "supplier_sku": "string|null",
      "pack_size_text": "string|null",
      "unit_basis": "kg|pack|piece|null",
      "min_order_qty": "number|null",
      "price": "number",
      "currency": "string|null",
      "price_type": "standard|promo|special",
      "min_qty": "number|null",
      "valid_from": "YYYY-MM-DD|null",
      "valid_to": "YYYY-MM-DD|null",
      "source_page": "number",
      "row_index": "number|null",
      "raw_row": "string|null"
    }}
  ]
}}

Rules:
1. Use null for missing fields; do not guess.
2. Use the exact field names shown above.
3. supplier_name_raw must be the full item description including specs like origin, brand, or pack size.
4. ONLY include an item if there is a clear numeric price on the page. If the page is a cover, delivery schedule/terms, contact info, or a product photo page without explicit prices, return {{"items": []}}.
5. Skip rows with missing price or "N/A" (do not output a price of 0).
6. price and min_order_qty/min_qty must be numbers (no currency symbols, no commas).
7. If item currency is missing, use the top-level currency.
8. If price_type is missing, use "standard".
9. If the page has a "code" column, map it to supplier_sku. If it has "packing", map it to pack_size_text. If it has a "unit" column: kg→unit_basis="kg"; pack→"pack"; jar/bottle/pc→"piece".
10. For each item, set source_page={page_num} and include row_index/raw_row when possible.
11. If the page has no relevant data, return {{"items": []}}.

Page content (Markdown):
{markdown_text}
"""
    else:
        prompt = f"""Extract data from this page ({page_num}) into JSON matching this database schema:

{db_schema}
{repair_block}

Page content (Markdown):
{markdown_text}

Return a JSON object with appropriate fields.
For line items/products, include source_page={page_num} for each item.
If the page has no relevant data, return {{"items": [], "line_items": []}}.
"""

    # Use text LLM, not vision model (cheaper & faster)
    return _extract_structured_from_text(prompt, markdown_text, settings)


def process_image_with_vision(
    file_bytes: bytes, prompt: str, settings: Settings, mime_type: str | None = None
) -> VisionCallResult:
    """
    Process an image with a vision model.

    Args:
        file_bytes: Image file bytes
        prompt: Prompt describing what to extract from the image
        settings: Application settings
        mime_type: Optional MIME type (image/jpeg, image/png, image/webp, image/gif)

    Returns:
        VisionCallResult with extracted content and telemetry data
    """
    print(
        f"[VISION] process_image_with_vision: starting, image_size={len(file_bytes):,} bytes, mime={mime_type}"
    )
    model, api_key, base_url = _get_vision_settings(settings)
    print(
        f"[VISION] process_image_with_vision: using model={model}, base_url={base_url}"
    )
    start_time = time.time()

    # Encode image as base64
    image_base64 = base64.b64encode(file_bytes).decode("utf-8")
    # Detect image format from MIME type, default to JPEG
    if mime_type and mime_type.startswith("image/"):
        image_format = mime_type
    else:
        image_format = "image/jpeg"  # Default format for base64 data URLs

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image_format};base64,{image_base64}",
                    },
                },
            ],
        }
    ]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    _apply_reasoning_policy(payload, settings, base_url)

    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.openai_timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    attempts = max(0, int(settings.openai_max_retries)) + 1
    retry_initial = float(settings.openai_retry_initial_seconds)
    retry_max = float(settings.openai_retry_max_seconds)

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if (
                resp.status_code >= 400
                and _is_retryable_status(resp.status_code)
                and attempt < attempts
            ):
                logger.warning(
                    "vision_retryable_status",
                    extra={
                        "status_code": resp.status_code,
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            resp.raise_for_status()
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise OpenAIError(f"Unexpected vision response: {data}")

            # Extract telemetry data
            from app.ai.openrouter_generation import extract_openrouter_generation_id
            from app.ai.openrouter_usage import extract_openrouter_usage

            generation_id = extract_openrouter_generation_id(
                headers=dict(resp.headers), data=data
            )
            usage = extract_openrouter_usage(data)

            return VisionCallResult(
                content=content,
                model=model,
                latency_ms=latency_ms,
                usage=usage,
                openrouter_generation_id=generation_id,
                response_headers=dict(resp.headers),
                response_data=data,
                error=None,
                prompt_text=prompt,
                request_payload=payload,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < attempts and _is_retryable_exception(exc):
                logger.warning(
                    "vision_retryable_error",
                    extra={
                        "error": repr(exc),
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            logger.exception("vision_http_error", extra={"error": repr(exc)})
            raise OpenAIError(f"Vision request failed: {exc}") from exc

    # Final error after all retries
    end_time = time.time()
    latency_ms = int((end_time - start_time) * 1000)
    raise OpenAIError(f"Vision request failed: {last_exc!r}")


def _is_text_based_pdf(file_bytes: bytes) -> bool:
    """
    Check if PDF is text-based (can extract text directly) or image-based (needs OCR).

    Args:
        file_bytes: PDF file bytes

    Returns:
        True if text-based, False if image-based
    """
    print(f"[VISION] _is_text_based_pdf: checking PDF type...")
    try:
        from pypdf import PdfReader

        pdf_reader = PdfReader(io.BytesIO(file_bytes))
        total_pages = len(pdf_reader.pages)
        print(f"[VISION] _is_text_based_pdf: PDF has {total_pages} pages")

        # Check first few pages for extractable text
        text_length = 0
        pages_to_check = min(3, total_pages)
        for i in range(pages_to_check):
            try:
                page_text = pdf_reader.pages[i].extract_text()
                if page_text:
                    text_length += len(page_text.strip())
            except Exception:
                pass

        is_text_based = text_length > 100
        print(
            f"[VISION] _is_text_based_pdf: extracted {text_length} chars from first {pages_to_check} pages -> {'text-based' if is_text_based else 'image-based'}"
        )
        return is_text_based
    except ImportError:
        print(
            "[VISION] _is_text_based_pdf: pypdf not available, assuming image-based PDF"
        )
        logger.warning("pypdf not available, assuming image-based PDF")
        return False
    except Exception as e:
        print(f"[VISION] _is_text_based_pdf: Error: {e}, assuming image-based")
        logger.warning(f"Error checking PDF type: {e}, assuming image-based")
        return False


def _extract_text_from_pdf_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    """
    Extract text from each page of a text-based PDF.

    Args:
        file_bytes: PDF file bytes

    Returns:
        List of (page_number, text) tuples (1-indexed)
    """
    print("[VISION] _extract_text_from_pdf_pages: starting text extraction...")
    try:
        from pypdf import PdfReader

        pdf_reader = PdfReader(io.BytesIO(file_bytes))
        total_pages = len(pdf_reader.pages)
        print(
            f"[VISION] _extract_text_from_pdf_pages: extracting text from {total_pages} pages"
        )

        pages = []
        for i, page in enumerate(pdf_reader.pages, start=1):
            try:
                text = page.extract_text()
                char_count = len(text) if text else 0
                print(
                    f"[VISION] _extract_text_from_pdf_pages: page {i}/{total_pages} - {char_count} chars"
                )
                pages.append((i, text))
            except Exception as e:
                print(
                    f"[VISION] _extract_text_from_pdf_pages: page {i}/{total_pages} - ERROR: {e}"
                )
                logger.warning(f"Error extracting text from page {i}: {e}")
                pages.append((i, ""))

        print(
            f"[VISION] _extract_text_from_pdf_pages: completed, {len(pages)} pages extracted"
        )
        return pages
    except ImportError:
        print("[VISION] _extract_text_from_pdf_pages: pypdf not available!")
        raise OpenAIError("pypdf is required for text-based PDF extraction")
    except Exception as e:
        print(f"[VISION] _extract_text_from_pdf_pages: FAILED: {e}")
        raise OpenAIError(f"Failed to extract text from PDF: {e}") from e


def _convert_pdf_pages_to_images(file_bytes: bytes) -> list[tuple[int, bytes]]:
    """
    Convert PDF pages to images.

    Args:
        file_bytes: PDF file bytes

    Returns:
        List of (page_number, image_bytes) tuples (1-indexed)
    """
    print("[VISION] _convert_pdf_pages_to_images: starting PDF to image conversion...")
    try:
        from pdf2image import convert_from_bytes

        print(
            "[VISION] _convert_pdf_pages_to_images: converting pages (this may take a while)..."
        )
        images = convert_from_bytes(file_bytes)
        total_pages = len(images)
        print(
            f"[VISION] _convert_pdf_pages_to_images: converted {total_pages} pages to images"
        )

        pages = []
        for i, img in enumerate(images, start=1):
            # Convert PIL Image to bytes
            img_bytes_io = io.BytesIO()
            img.save(img_bytes_io, format="PNG")
            img_size = len(img_bytes_io.getvalue())
            print(
                f"[VISION] _convert_pdf_pages_to_images: page {i}/{total_pages} - {img_size:,} bytes"
            )
            pages.append((i, img_bytes_io.getvalue()))

        print(
            f"[VISION] _convert_pdf_pages_to_images: completed, {len(pages)} images ready"
        )
        return pages
    except ImportError:
        print("[VISION] _convert_pdf_pages_to_images: pdf2image not available!")
        raise OpenAIError(
            "pdf2image and Pillow are required for image-based PDF processing"
        )
    except Exception as e:
        print(f"[VISION] _convert_pdf_pages_to_images: FAILED: {e}")
        raise OpenAIError(f"Failed to convert PDF pages to images: {e}") from e


def process_pdf_with_vision(
    file_bytes: bytes,
    filename: str | None,
    prompt: str,
    settings: Settings,
    page_by_page: bool = True,
    structured_chunk_size: int | None = None,
) -> VisionDocumentResult:
    """
    Process a PDF file with a vision model, optionally page-by-page.

    Args:
        file_bytes: PDF file bytes
        filename: Optional filename for the PDF
        prompt: Prompt describing what to extract from the PDF
        settings: Application settings
        page_by_page: If True, process each page separately and consolidate
        structured_chunk_size: Optional override for how many pages per extraction chunk

    Returns:
        VisionDocumentResult with consolidated content and telemetry for all pages
    """
    print(f"\n[VISION] process_pdf_with_vision: starting...")
    print(
        f"[VISION] process_pdf_with_vision: filename={filename}, page_by_page={page_by_page}"
    )
    print(f"[VISION] process_pdf_with_vision: file_size={len(file_bytes):,} bytes")

    if not page_by_page:
        # Original behavior: process entire PDF at once
        result = _process_pdf_single(file_bytes, filename, prompt, settings)
        return VisionDocumentResult(
            content=result.content,
            telemetry_results=[result],
        )

    # Page-by-page processing with combined text + OCR
    print("[VISION] process_pdf_with_vision: extracting text + OCR per page...")

    text_pages: list[tuple[int, str]] = []
    try:
        text_pages = _extract_text_from_pdf_pages(file_bytes)
    except OpenAIError as exc:
        logger.warning("text_extraction_failed_using_ocr_only", extra={"error": str(exc)})

    image_pages = _convert_pdf_pages_to_images(file_bytes)
    total_pages = len(image_pages)
    text_by_page = {page_num: text for page_num, text in text_pages}

    telemetry_results: list[VisionCallResult] = []
    ocr_by_page: dict[int, str] = {}

    ocr_prompt = (
        "Extract all readable text from this page. Preserve line breaks and table "
        "structure as best as possible. Do not summarize or omit headers/footers. "
        "Return Markdown only (use tables where appropriate, keep headings)."
    )

    for page_num, page_image_bytes in image_pages:
        print(
            f"[VISION] process_pdf_with_vision: page {page_num}/{total_pages} - OCR ({len(page_image_bytes):,} bytes)..."
        )
        try:
            result = process_image_with_vision(
                page_image_bytes, ocr_prompt, settings, "image/png"
            )
            telemetry_results.append(result)
            ocr_by_page[page_num] = result.content or ""
            print(
                f"[VISION] process_pdf_with_vision: page {page_num}/{total_pages} - ✓ OCR ({result.latency_ms}ms)"
            )
        except Exception as exc:
            logger.warning(
                "ocr_page_failed",
                extra={"page": page_num, "error": str(exc)},
            )
            error_result = VisionCallResult(
                content="",
                model=_get_vision_settings(settings)[0],
                latency_ms=0,
                usage=None,
                openrouter_generation_id=None,
                response_headers={},
                response_data={},
                error=f"OCR failed for page {page_num}: {exc}",
            )
            telemetry_results.append(error_result)
            ocr_by_page[page_num] = ""

    page_text_blocks = []
    for page_num, _ in image_pages:
        text_layer = text_by_page.get(page_num, "")
        ocr_layer = ocr_by_page.get(page_num, "")
        _log_page_text(page_num, text_layer, ocr_layer)
        page_text_blocks.append(
            "\n".join(
                [
                    f"## Page {page_num}",
                    "Text layer:",
                    text_layer if text_layer.strip() else "[EMPTY]",
                    "OCR layer:",
                    ocr_layer if ocr_layer.strip() else "[EMPTY]",
                ]
            )
        )

    combined_text = "\n\n".join(page_text_blocks)
    _log_combined_text(combined_text)

    if structured_chunk_size is not None:
        chunk_size = max(1, int(structured_chunk_size))
    else:
        chunk_size = max(1, int(getattr(settings, "vision_pdf_chunk_size", 3) or 1))
    chunk_results: list[dict[str, Any]] = []
    total_chunks = (len(page_text_blocks) + chunk_size - 1) // chunk_size

    for start in range(0, len(page_text_blocks), chunk_size):
        end = min(start + chunk_size, len(page_text_blocks))
        chunk_index = (start // chunk_size) + 1
        chunk_text = "\n\n".join(page_text_blocks[start:end])
        print(
            f"[VISION] process_pdf_with_vision: chunk {chunk_index}/{total_chunks} - structured extraction..."
        )
        structured_result = _extract_structured_from_text(
            prompt, chunk_text, settings
        )
        telemetry_results.append(structured_result)
        _log_structured_text(structured_result.content)
        if structured_result.error:
            raise OpenAIError(structured_result.error)
        chunk_results.append(_parse_structured_json(structured_result.content))

    merged = _merge_structured_results(chunk_results)
    merged_content = json.dumps(merged, ensure_ascii=False)

    print(
        f"[VISION] process_pdf_with_vision: ✓ aggregation complete, {len(merged_content)} chars"
    )
    return VisionDocumentResult(
        content=merged_content,
        telemetry_results=telemetry_results,
    )


def _process_pdf_single(
    file_bytes: bytes, filename: str | None, prompt: str, settings: Settings
) -> VisionCallResult:
    """
    Process entire PDF at once (original behavior).

    Args:
        file_bytes: PDF file bytes
        filename: Optional filename for the PDF
        prompt: Prompt describing what to extract from the PDF
        settings: Application settings

    Returns:
        VisionCallResult with extracted content and telemetry data
    """
    model, api_key, base_url = _get_vision_settings(settings)
    start_time = time.time()

    # Encode PDF as base64
    file_base64 = base64.b64encode(file_bytes).decode("utf-8")
    data_url = f"data:application/pdf;base64,{file_base64}"

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title

    # OpenRouter PDF format: use "type": "file" with file object
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "file",
                    "file": {
                        "filename": filename or "document.pdf",
                        "file_data": data_url,
                    },
                },
            ],
        }
    ]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    _apply_reasoning_policy(payload, settings, base_url)

    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.openai_timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    attempts = max(0, int(settings.openai_max_retries)) + 1
    retry_initial = float(settings.openai_retry_initial_seconds)
    retry_max = float(settings.openai_retry_max_seconds)

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if (
                resp.status_code >= 400
                and _is_retryable_status(resp.status_code)
                and attempt < attempts
            ):
                logger.warning(
                    "vision_retryable_status",
                    extra={
                        "status_code": resp.status_code,
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            resp.raise_for_status()
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise OpenAIError(f"Unexpected vision response: {data}")

            # Extract telemetry data
            from app.ai.openrouter_generation import extract_openrouter_generation_id
            from app.ai.openrouter_usage import extract_openrouter_usage

            generation_id = extract_openrouter_generation_id(
                headers=dict(resp.headers), data=data
            )
            usage = extract_openrouter_usage(data)

            return VisionCallResult(
                content=content,
                model=model,
                latency_ms=latency_ms,
                usage=usage,
                openrouter_generation_id=generation_id,
                response_headers=dict(resp.headers),
                response_data=data,
                error=None,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < attempts and _is_retryable_exception(exc):
                logger.warning(
                    "vision_retryable_error",
                    extra={
                        "error": repr(exc),
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            logger.exception("vision_http_error", extra={"error": repr(exc)})
            raise OpenAIError(f"Vision request failed: {exc}") from exc

    # Final error after all retries
    end_time = time.time()
    latency_ms = int((end_time - start_time) * 1000)
    raise OpenAIError(f"Vision request failed: {last_exc!r}")


def _consolidate_pdf_page_results(
    page_results: list[tuple[int, str]], prompt: str, settings: Settings
) -> str:
    """
    Consolidate results from multiple PDF pages into a single structured response.

    Args:
        page_results: List of (page_number, extracted_json_string) tuples
        prompt: Original extraction prompt
        settings: Application settings

    Returns:
        Consolidated JSON string
    """
    if not page_results:
        raise OpenAIError("No pages were successfully processed")

    if len(page_results) == 1:
        # Single page, return as-is
        return page_results[0][1]

    # Parse all page results
    parsed_results = []
    for page_num, result_str in page_results:
        try:
            # Clean JSON string
            cleaned = result_str.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
            if cleaned.startswith("```json"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

            data = json.loads(cleaned)
            parsed_results.append((page_num, data))
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse page {page_num} result: {e}")
            continue

    if not parsed_results:
        raise OpenAIError("No valid page results to consolidate")

    # Consolidate: merge line_items/items from all pages, keep header/metadata from first page
    consolidated = parsed_results[0][1].copy()  # Start with first page data

    # Merge arrays (line_items for invoices, items for price lists/inventory)
    array_keys = ["line_items", "items"]
    for key in array_keys:
        if key in consolidated:
            all_items = consolidated[key].copy()
            # Add items from other pages
            for page_num, page_data in parsed_results[1:]:
                if key in page_data:
                    for item in page_data[key]:
                        # Ensure source_page is set
                        if "source_page" not in item:
                            item["source_page"] = page_num
                        all_items.append(item)

            # De-duplicate items based on DB column matching
            if key == "line_items":
                # For invoices: de-dupe on description_raw + quantity + unit + unit_price
                deduplicated = []
                seen = set()
                for item in all_items:
                    desc = item.get("description_raw", item.get("description", ""))
                    qty = item.get("quantity")
                    unit = item.get("unit", "")
                    price = item.get("unit_price")
                    # Create key for deduplication
                    dedup_key = (desc, qty, unit, price)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        deduplicated.append(item)
                all_items = deduplicated
            elif key == "items":
                # For price lists: de-dupe on supplier_name_raw + pack_size_text + unit_basis + min_order_qty
                # Note: We preserve items with different prices even if other fields match
                deduplicated = []
                seen = set()
                for item in all_items:
                    name = item.get("supplier_name_raw", item.get("name", ""))
                    pack_size = item.get("pack_size_text", "")
                    unit_basis = item.get("unit_basis", "")
                    min_order_qty = item.get("min_order_qty")
                    # Create key for deduplication (excluding price to preserve variants)
                    dedup_key = (name, pack_size, unit_basis, min_order_qty)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        deduplicated.append(item)
                all_items = deduplicated

            consolidated[key] = all_items

    # Return consolidated JSON
    return json.dumps(consolidated, ensure_ascii=False)


def extract_text_from_file(
    file_bytes: bytes, mime_type: str, filename: str | None
) -> str:
    """
    Extract text from text-based files (TXT, CSV, etc.) without using vision model.

    Args:
        file_bytes: File bytes
        mime_type: MIME type of the file
        filename: Optional filename (used to determine file type if MIME type is ambiguous)

    Returns:
        Extracted text content
    """
    import csv
    import io

    # Handle plain text files
    if mime_type == "text/plain" or (filename and filename.endswith(".txt")):
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return file_bytes.decode("latin-1")
            except UnicodeDecodeError:
                raise OpenAIError("Could not decode text file as UTF-8 or Latin-1")

    # Handle CSV files
    if mime_type == "text/csv" or (filename and filename.endswith(".csv")):
        try:
            text = file_bytes.decode("utf-8")
            # Parse CSV and format as readable text
            csv_reader = csv.reader(io.StringIO(text))
            rows = []
            for row in csv_reader:
                rows.append(" | ".join(row))
            return "\n".join(rows)
        except Exception as e:
            logger.exception("csv_extraction_failed")
            raise OpenAIError(f"Failed to extract text from CSV: {e}") from e

    # Handle Excel files (XLS, XLSX) - requires pandas or openpyxl
    if mime_type in (
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) or (filename and (filename.endswith(".xls") or filename.endswith(".xlsx"))):
        try:
            import pandas as pd
        except ImportError:
            raise OpenAIError(
                "Excel file processing requires pandas. Install with: pip install pandas openpyxl"
            )

        try:
            # Read Excel file into DataFrame
            df = pd.read_excel(
                io.BytesIO(file_bytes),
                engine="openpyxl" if filename and filename.endswith(".xlsx") else None,
            )
            # Convert to CSV-like text format
            return df.to_string(index=False)
        except Exception as e:
            logger.exception("excel_extraction_failed")
            raise OpenAIError(f"Failed to extract text from Excel file: {e}") from e

    # Handle Word documents (.docx) - requires python-docx
    if (
        mime_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or (filename and filename.endswith(".docx"))
    ):
        try:
            from docx import Document
        except ImportError:
            raise OpenAIError(
                "Word document (.docx) processing requires python-docx. Install with: pip install python-docx"
            )

        try:
            # Read .docx file
            doc = Document(io.BytesIO(file_bytes))
            # Extract all paragraphs
            paragraphs = []
            for para in doc.paragraphs:
                if para.text.strip():
                    paragraphs.append(para.text)
            # Extract text from tables
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells)
                    if row_text.strip():
                        paragraphs.append(row_text)
            return "\n".join(paragraphs)
        except Exception as e:
            logger.exception("docx_extraction_failed")
            raise OpenAIError(
                f"Failed to extract text from Word document (.docx): {e}"
            ) from e

    # Handle legacy Word documents (.doc) - requires docx2txt or similar
    if mime_type == "application/msword" or (filename and filename.endswith(".doc")):
        try:
            import docx2txt
        except ImportError:
            raise OpenAIError(
                "Legacy Word document (.doc) processing requires docx2txt. Install with: pip install docx2txt"
            )

        try:
            # docx2txt can handle both .doc and .docx, but .doc support is limited
            # Save to temporary location for docx2txt to process
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(delete=False, suffix=".doc") as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = tmp_file.name

            try:
                text = docx2txt.process(tmp_path)
                return text if text else ""
            finally:
                # Clean up temporary file
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as e:
            logger.exception("doc_extraction_failed")
            raise OpenAIError(
                f"Failed to extract text from legacy Word document (.doc): {e}"
            ) from e

    raise OpenAIError(f"Unsupported text file type: {mime_type}")


def _extract_structured_from_text(
    prompt: str,
    extracted_text: str,
    settings: Settings,
) -> VisionCallResult:
    from app.ai.openrouter_generation import extract_openrouter_generation_id
    from app.ai.openrouter_usage import extract_openrouter_usage

    system_prompt = (
        "You are a helpful assistant that extracts structured data from text. "
        "Return ONLY valid JSON, no other text."
    )
    user_prompt = f"{prompt}\n\nExtracted text:\n{extracted_text}"

    model, api_key, base_url = _get_text_settings(settings)
    start_time = time.time()
    payload: dict[str, Any] | None = None
    try:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if settings.openrouter_http_referer:
            headers["HTTP-Referer"] = settings.openrouter_http_referer
        if settings.openrouter_title:
            headers["X-Title"] = settings.openrouter_title

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        # Prefer disabling reasoning for fast/cheap extraction. Some providers may
        # reject this, so fall back to low-effort reasoning on 4xx errors.
        def _post_once(local_payload: dict[str, Any]) -> httpx.Response:
            timeout = httpx.Timeout(
                connect=10.0,
                read=float(settings.openai_timeout_seconds),
                write=10.0,
                pool=10.0,
            )
            return httpx.post(url, headers=headers, json=local_payload, timeout=timeout)

        should_control_reasoning = _should_control_reasoning(base_url)
        if should_control_reasoning:
            payload["reasoning"] = {"enabled": False}

        resp = _post_once(payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            if should_control_reasoning:
                # Retry once with low-effort reasoning (OpenRouter default policy) if the
                # upstream rejects "enabled": false.
                payload.pop("reasoning", None)
                _apply_reasoning_policy(payload, settings, base_url)
                resp = _post_once(payload)
                resp.raise_for_status()
            else:
                raise
        response = resp.json()
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        content = response.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise OpenAIError(f"Unexpected LLM response: {response}")

        generation_id = extract_openrouter_generation_id(
            headers=dict(resp.headers), data=response
        )
        usage = extract_openrouter_usage(response)

        return VisionCallResult(
            content=content,
            model=model,
            latency_ms=latency_ms,
            usage=usage,
            openrouter_generation_id=generation_id,
            response_headers=dict(resp.headers),
            response_data=response,
            error=None,
            prompt_text=prompt,
            input_text=extracted_text,
            request_payload=payload,
        )
    except Exception as exc:
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)
        logger.exception("text_extraction_failed")
        return VisionCallResult(
            content="",
            model=model,
            latency_ms=latency_ms,
            usage=None,
            openrouter_generation_id=None,
            response_headers={},
            response_data={},
            error=f"Text extraction failed: {exc}",
            prompt_text=prompt,
            input_text=extracted_text,
            request_payload=payload,
        )


def process_document_with_vision(
    file_bytes: bytes,
    mime_type: str,
    prompt: str,
    settings: Settings,
    filename: str | None = None,
    structured_chunk_size: int | None = None,
) -> VisionDocumentResult:
    """
    Process a document (PDF, image, text file, etc.) appropriately.

    Args:
        file_bytes: Document file bytes
        mime_type: MIME type of the document (e.g., "application/pdf", "image/jpeg", "text/csv")
        prompt: Prompt describing what to extract from the document
        settings: Application settings
        filename: Optional filename (used for PDFs and to determine file type)
        structured_chunk_size: Optional override for how many pages per extraction chunk

    Returns:
        VisionDocumentResult with extracted content and telemetry data
    """
    print(f"\n[VISION] ========== process_document_with_vision ==========")
    print(f"[VISION] mime_type: {mime_type}")
    print(f"[VISION] filename: {filename}")
    print(f"[VISION] file_size: {len(file_bytes):,} bytes")

    # For images, use the image processing function
    if mime_type.startswith("image/"):
        print(f"[VISION] -> Processing as IMAGE")
        result = process_image_with_vision(file_bytes, prompt, settings, mime_type)
        return VisionDocumentResult(
            content=result.content,
            telemetry_results=[result],
        )

    # For PDFs, use page-by-page processing
    if mime_type == "application/pdf":
        print(f"[VISION] -> Processing as PDF (page-by-page)")
        return process_pdf_with_vision(
            file_bytes,
            filename,
            prompt,
            settings,
            page_by_page=True,
            structured_chunk_size=structured_chunk_size,
        )

    # For text files, extract text directly and send to LLM for processing
    if (
        mime_type.startswith("text/")
        or mime_type
        in (
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        or (
            filename
            and any(
                filename.endswith(ext)
                for ext in [".txt", ".csv", ".xls", ".xlsx", ".doc", ".docx"]
            )
        )
    ):
        # Extract text from file
        extracted_text = extract_text_from_file(file_bytes, mime_type, filename)

        telemetry_result = _extract_structured_from_text(
            prompt, extracted_text, settings
        )
        if telemetry_result.error:
            raise OpenAIError(telemetry_result.error)
        return VisionDocumentResult(
            content=telemetry_result.content,
            telemetry_results=[telemetry_result],
        )

    raise OpenAIError(f"Unsupported document type: {mime_type}")
