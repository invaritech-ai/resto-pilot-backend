from __future__ import annotations

import base64
import logging
import random
import time
from typing import Any

import httpx

from app.ai.openai_client import (
    OpenAIError,
    _is_retryable_exception,
    _is_retryable_status,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)


def _get_vision_settings(settings: Settings) -> tuple[str, str, str]:
    """Get vision model settings, falling back to OpenAI defaults if not configured."""
    model = settings.vision_model or settings.openai_model
    api_key = settings.vision_api_key or settings.openai_api_key
    base_url = settings.vision_base_url or settings.openai_base_url

    if not api_key:
        raise OpenAIError(
            "Vision API key is not configured (APP_VISION_API_KEY or APP_OPENAI_API_KEY)"
        )

    return model, api_key, base_url


def process_image_with_vision(
    file_bytes: bytes, prompt: str, settings: Settings
) -> str:
    """
    Process an image with a vision model.

    Args:
        file_bytes: Image file bytes
        prompt: Prompt describing what to extract from the image
        settings: Application settings

    Returns:
        Extracted text/structured data as string
    """
    model, api_key, base_url = _get_vision_settings(settings)

    # Encode image as base64
    image_base64 = base64.b64encode(file_bytes).decode("utf-8")
    # Determine image format from bytes (simplified - assumes JPEG/PNG)
    image_format = "image/jpeg"  # Default, could be enhanced to detect actual format

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
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise OpenAIError(f"Unexpected vision response: {data}")
            return content
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

    raise OpenAIError(f"Vision request failed: {last_exc!r}")


def process_document_with_vision(
    file_bytes: bytes, mime_type: str, prompt: str, settings: Settings
) -> str:
    """
    Process a document (PDF, image, etc.) with a vision model.

    Args:
        file_bytes: Document file bytes
        mime_type: MIME type of the document (e.g., "application/pdf", "image/jpeg")
        prompt: Prompt describing what to extract from the document
        settings: Application settings

    Returns:
        Extracted text/structured data as string
    """
    # For PDFs, we'd need to convert to images first
    # For now, treat as image if it's an image type
    if mime_type.startswith("image/"):
        return process_image_with_vision(file_bytes, prompt, settings)

    # For PDFs and other formats, we'd need additional processing
    # For now, raise an error for unsupported types
    if mime_type == "application/pdf":
        # TODO: Convert PDF pages to images and process each page
        # For now, treat as unsupported
        raise OpenAIError(f"PDF processing not yet implemented. MIME type: {mime_type}")

    raise OpenAIError(f"Unsupported document type: {mime_type}")
