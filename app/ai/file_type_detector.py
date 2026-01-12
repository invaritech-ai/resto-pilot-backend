"""
File type detection using vision LLM.

Detects whether an uploaded file is a price list or an invoice.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.ai.model_config import get_file_type_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class FileTypeDetectionResult:
    """Result of file type detection."""

    file_type: str  # "price_list", "invoice", or "unknown"
    confidence: float
    reason: str | None = None

    # Telemetry
    model: str = ""
    latency_ms: int = 0
    generation_id: str | None = None
    usage: dict[str, Any] | None = None


DETECTION_PROMPT = """Analyze this file and determine if it's a PRICE LIST or an INVOICE.

PRICE LIST characteristics:
- List of products/items with prices
- Usually from a supplier/vendor
- Contains product names, units, prices
- May have multiple items listed
- Often titled "Price List", "Rate Card", "Catalog", "Product List"

INVOICE characteristics:
- Bill for goods/services already delivered
- Has invoice number, date, payment terms
- Lists specific quantities ordered/delivered
- Shows totals, taxes, payment due
- Often titled "Invoice", "Bill", "Tax Invoice", "Receipt"

Return ONLY valid JSON:
{
  "file_type": "price_list" or "invoice" or "unknown",
  "confidence": 0.0 to 1.0,
  "reason": "brief explanation"
}
"""


def detect_file_type_from_text(
    *,
    text_content: str,
    caption: str | None,
    filename: str | None,
    settings: Settings,
) -> FileTypeDetectionResult:
    """
    Detect file type from extracted text content.

    This is used when we have already extracted text from a PDF/image.

    Args:
        text_content: Extracted text from the file
        caption: Optional caption provided by user
        filename: Original filename
        settings: App settings

    Returns:
        FileTypeDetectionResult with detected type and confidence
    """
    # Quick heuristics from filename
    if filename:
        fn_lower = filename.lower()
        if any(kw in fn_lower for kw in ["price", "rate", "catalog", "pricelist"]):
            return FileTypeDetectionResult(
                file_type="price_list",
                confidence=0.9,
                reason="Filename indicates price list",
            )
        if any(kw in fn_lower for kw in ["invoice", "bill", "receipt", "challan"]):
            return FileTypeDetectionResult(
                file_type="invoice",
                confidence=0.9,
                reason="Filename indicates invoice",
            )

    # Quick heuristics from caption
    if caption:
        cap_lower = caption.lower()
        if any(kw in cap_lower for kw in ["price list", "pricelist", "prices", "rate card", "catalog"]):
            return FileTypeDetectionResult(
                file_type="price_list",
                confidence=0.95,
                reason="User indicated price list",
            )
        if any(kw in cap_lower for kw in ["invoice", "bill", "receipt"]):
            return FileTypeDetectionResult(
                file_type="invoice",
                confidence=0.95,
                reason="User indicated invoice",
            )

    # Use LLM for detection
    file_type_model = get_file_type_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": file_type_model})
        if file_type_model != settings.openai_model
        else settings
    )

    # Truncate text to avoid token limits
    truncated_text = text_content[:3000] if len(text_content) > 3000 else text_content

    user_prompt = f"""File to analyze:
{truncated_text}

{f'Filename: {filename}' if filename else ''}
{f'User caption: {caption}' if caption else ''}
"""

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=gate_settings,
            messages=[
                {"role": "system", "content": DETECTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except OpenAIError as e:
        logger.exception("file_type_detection_failed", extra={"error": str(e)})
        return FileTypeDetectionResult(
            file_type="unknown",
            confidence=0.0,
            reason=f"Detection failed: {e}",
            model=file_type_model,
        )

    # Extract telemetry
    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", file_type_model)

    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Empty content")

        # Parse JSON - handle markdown code blocks
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines if not line.startswith("```")
            )

        parsed = json.loads(content)
        file_type = parsed.get("file_type", "unknown")
        confidence = float(parsed.get("confidence", 0.5))
        reason = parsed.get("reason")

        # Validate file_type
        if file_type not in ("price_list", "invoice", "unknown"):
            file_type = "unknown"

        return FileTypeDetectionResult(
            file_type=file_type,
            confidence=confidence,
            reason=reason,
            model=model_used if isinstance(model_used, str) else file_type_model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else None,
        )

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning(
            "file_type_detection_parse_failed",
            extra={"error": str(e)},
        )
        return FileTypeDetectionResult(
            file_type="unknown",
            confidence=0.0,
            reason=f"Parse failed: {e}",
            model=model_used if "model_used" in dir() else file_type_model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if "usage" in dir() and isinstance(usage, dict) else None,
        )
