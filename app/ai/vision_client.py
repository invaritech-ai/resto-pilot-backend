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
    _is_retryable_exception,
    _is_retryable_status,
    chat_completions_create,
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


@dataclass
class VisionDocumentResult:
    """Result from processing a document with telemetry data."""

    content: str
    telemetry_results: list[VisionCallResult]


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
    model, api_key, base_url = _get_vision_settings(settings)
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


def _is_text_based_pdf(file_bytes: bytes) -> bool:
    """
    Check if PDF is text-based (can extract text directly) or image-based (needs OCR).

    Args:
        file_bytes: PDF file bytes

    Returns:
        True if text-based, False if image-based
    """
    try:
        from pypdf import PdfReader

        pdf_reader = PdfReader(io.BytesIO(file_bytes))
        # Check first few pages for extractable text
        text_length = 0
        pages_to_check = min(3, len(pdf_reader.pages))
        for i in range(pages_to_check):
            try:
                page_text = pdf_reader.pages[i].extract_text()
                if page_text:
                    text_length += len(page_text.strip())
            except Exception:
                pass

        # If we can extract substantial text, it's text-based
        return text_length > 100
    except ImportError:
        logger.warning("pypdf not available, assuming image-based PDF")
        return False
    except Exception as e:
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
    try:
        from pypdf import PdfReader

        pdf_reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for i, page in enumerate(pdf_reader.pages, start=1):
            try:
                text = page.extract_text()
                pages.append((i, text))
            except Exception as e:
                logger.warning(f"Error extracting text from page {i}: {e}")
                pages.append((i, ""))
        return pages
    except ImportError:
        raise OpenAIError("pypdf is required for text-based PDF extraction")
    except Exception as e:
        raise OpenAIError(f"Failed to extract text from PDF: {e}") from e


def _convert_pdf_pages_to_images(file_bytes: bytes) -> list[tuple[int, bytes]]:
    """
    Convert PDF pages to images.

    Args:
        file_bytes: PDF file bytes

    Returns:
        List of (page_number, image_bytes) tuples (1-indexed)
    """
    try:
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(file_bytes)
        pages = []
        for i, img in enumerate(images, start=1):
            # Convert PIL Image to bytes
            img_bytes_io = io.BytesIO()
            img.save(img_bytes_io, format="PNG")
            pages.append((i, img_bytes_io.getvalue()))
        return pages
    except ImportError:
        raise OpenAIError(
            "pdf2image and Pillow are required for image-based PDF processing"
        )
    except Exception as e:
        raise OpenAIError(f"Failed to convert PDF pages to images: {e}") from e


def process_pdf_with_vision(
    file_bytes: bytes,
    filename: str | None,
    prompt: str,
    settings: Settings,
    page_by_page: bool = True,
) -> VisionDocumentResult:
    """
    Process a PDF file with a vision model, optionally page-by-page.

    Args:
        file_bytes: PDF file bytes
        filename: Optional filename for the PDF
        prompt: Prompt describing what to extract from the PDF
        settings: Application settings
        page_by_page: If True, process each page separately and consolidate

    Returns:
        VisionDocumentResult with consolidated content and telemetry for all pages
    """
    if not page_by_page:
        # Original behavior: process entire PDF at once
        result = _process_pdf_single(file_bytes, filename, prompt, settings)
        return VisionDocumentResult(
            content=result.content,
            telemetry_results=[result],
        )

    # Page-by-page processing
    is_text_based = _is_text_based_pdf(file_bytes)
    model, _, _ = _get_vision_settings(settings)
    telemetry_results: list[VisionCallResult] = []

    if is_text_based:
        # Extract text from each page
        pages = _extract_text_from_pdf_pages(file_bytes)
        page_results = []

        for page_num, page_text in pages:
            if not page_text.strip():
                continue

            # Process text with LLM
            system_prompt = "You are a helpful assistant that extracts structured data from text. Return ONLY valid JSON, no other text."
            user_prompt = (
                f"{prompt}\n\nExtracted text from page {page_num}:\n{page_text}"
            )

            try:
                start_time = time.time()
                response = chat_completions_create(
                    settings=settings,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                )
                end_time = time.time()
                latency_ms = int((end_time - start_time) * 1000)

                content = (
                    response.get("choices", [{}])[0].get("message", {}).get("content")
                )
                if not isinstance(content, str):
                    raise OpenAIError(f"Unexpected LLM response: {response}")

                # Extract telemetry
                from app.ai.openrouter_generation import (
                    extract_openrouter_generation_id,
                )
                from app.ai.openrouter_usage import extract_openrouter_usage

                generation_id = extract_openrouter_generation_id(data=response)
                usage = extract_openrouter_usage(response)

                telemetry_result = VisionCallResult(
                    content=content,
                    model=model,
                    latency_ms=latency_ms,
                    usage=usage,
                    openrouter_generation_id=generation_id,
                    response_headers={},
                    response_data=response,
                    error=None,
                )
                telemetry_results.append(telemetry_result)
                page_results.append((page_num, content))
            except Exception as e:
                logger.warning(f"Error processing page {page_num}: {e}")
                end_time = time.time()
                latency_ms = (
                    int((end_time - start_time) * 1000)
                    if "start_time" in locals()
                    else 0
                )
                error_result = VisionCallResult(
                    content="",
                    model=model,
                    latency_ms=latency_ms,
                    usage=None,
                    openrouter_generation_id=None,
                    response_headers={},
                    response_data={},
                    error=f"Error processing page {page_num}: {e}",
                )
                telemetry_results.append(error_result)
                continue

    else:
        # Convert pages to images and process with vision API
        pages = _convert_pdf_pages_to_images(file_bytes)
        page_results = []

        for page_num, page_image_bytes in pages:
            try:
                result = process_image_with_vision(
                    page_image_bytes, prompt, settings, "image/png"
                )
                telemetry_results.append(result)
                page_results.append((page_num, result.content))
            except Exception as e:
                logger.warning(f"Error processing page {page_num}: {e}")
                # Error telemetry already recorded in process_image_with_vision
                continue

    # Consolidate results
    consolidated_content = _consolidate_pdf_page_results(page_results, prompt, settings)
    return VisionDocumentResult(
        content=consolidated_content,
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


def process_document_with_vision(
    file_bytes: bytes,
    mime_type: str,
    prompt: str,
    settings: Settings,
    filename: str | None = None,
) -> VisionDocumentResult:
    """
    Process a document (PDF, image, text file, etc.) appropriately.

    Args:
        file_bytes: Document file bytes
        mime_type: MIME type of the document (e.g., "application/pdf", "image/jpeg", "text/csv")
        prompt: Prompt describing what to extract from the document
        settings: Application settings
        filename: Optional filename (used for PDFs and to determine file type)

    Returns:
        VisionDocumentResult with extracted content and telemetry data
    """
    # For images, use the image processing function
    if mime_type.startswith("image/"):
        result = process_image_with_vision(file_bytes, prompt, settings, mime_type)
        return VisionDocumentResult(
            content=result.content,
            telemetry_results=[result],
        )

    # For PDFs, use page-by-page processing
    if mime_type == "application/pdf":
        return process_pdf_with_vision(
            file_bytes, filename, prompt, settings, page_by_page=True
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

        # Send extracted text to LLM for structured extraction
        from app.ai.openai_client import chat_completions_create
        from app.ai.openrouter_generation import extract_openrouter_generation_id
        from app.ai.openrouter_usage import extract_openrouter_usage

        system_prompt = "You are a helpful assistant that extracts structured data from text. Return ONLY valid JSON, no other text."
        user_prompt = f"{prompt}\n\nExtracted text:\n{extracted_text}"

        model = settings.vision_model or settings.openai_model
        start_time = time.time()
        try:
            response = chat_completions_create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                settings=settings,
            )
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)

            content = response.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise OpenAIError(f"Unexpected LLM response: {response}")

            generation_id = extract_openrouter_generation_id(data=response)
            usage = extract_openrouter_usage(response)

            telemetry_result = VisionCallResult(
                content=content,
                model=model,
                latency_ms=latency_ms,
                usage=usage,
                openrouter_generation_id=generation_id,
                response_headers={},
                response_data=response,
                error=None,
            )
            return VisionDocumentResult(
                content=content,
                telemetry_results=[telemetry_result],
            )
        except Exception as e:
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.exception("text_file_llm_processing_failed")
            # Error telemetry will be recorded by caller if needed
            raise OpenAIError(f"Failed to process extracted text: {e}") from e

    raise OpenAIError(f"Unsupported document type: {mime_type}")
