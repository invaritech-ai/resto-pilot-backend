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
    file_bytes: bytes, prompt: str, settings: Settings, mime_type: str | None = None
) -> str:
    """
    Process an image with a vision model.

    Args:
        file_bytes: Image file bytes
        prompt: Prompt describing what to extract from the image
        settings: Application settings
        mime_type: Optional MIME type (image/jpeg, image/png, image/webp, image/gif)

    Returns:
        Extracted text/structured data as string
    """
    model, api_key, base_url = _get_vision_settings(settings)

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


def process_pdf_with_vision(
    file_bytes: bytes, filename: str | None, prompt: str, settings: Settings
) -> str:
    """
    Process a PDF file with a vision model using OpenRouter's file format.

    Args:
        file_bytes: PDF file bytes
        filename: Optional filename for the PDF
        prompt: Prompt describing what to extract from the PDF
        settings: Application settings

    Returns:
        Extracted text/structured data as string
    """
    model, api_key, base_url = _get_vision_settings(settings)

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


def process_document_with_vision(
    file_bytes: bytes,
    mime_type: str,
    prompt: str,
    settings: Settings,
    filename: str | None = None,
) -> str:
    """
    Process a document (PDF, image, text file, etc.) appropriately.

    Args:
        file_bytes: Document file bytes
        mime_type: MIME type of the document (e.g., "application/pdf", "image/jpeg", "text/csv")
        prompt: Prompt describing what to extract from the document
        settings: Application settings
        filename: Optional filename (used for PDFs and to determine file type)

    Returns:
        Extracted text/structured data as string
    """
    # For images, use the image processing function
    if mime_type.startswith("image/"):
        return process_image_with_vision(file_bytes, prompt, settings, mime_type)

    # For PDFs, use OpenRouter's file format
    if mime_type == "application/pdf":
        return process_pdf_with_vision(file_bytes, filename, prompt, settings)

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

        # Send extracted text to LLM for structured extraction
        from app.ai.openai_client import chat_completions_create

        system_prompt = "You are a helpful assistant that extracts structured data from text. Return ONLY valid JSON, no other text."
        user_prompt = f"{prompt}\n\nExtracted text:\n{extracted_text}"

        try:
            response = chat_completions_create(
                model=settings.vision_model or settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                settings=settings,
            )
            return response
        except Exception as e:
            logger.exception("text_file_llm_processing_failed")
            raise OpenAIError(f"Failed to process extracted text: {e}") from e

    raise OpenAIError(f"Unsupported document type: {mime_type}")
