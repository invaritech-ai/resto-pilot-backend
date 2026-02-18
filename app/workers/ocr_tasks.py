"""
Celery task: OCR + LLM extraction for uploaded files.

process_file_task(staging_id_hex, document_type, chat_id, progress_message_id=None):
  1. Download file from Telegram
  2. PDF → pdfplumber + camelot combined text → LLM parse
            < 5 items → pdf2image vision fallback
     Image → base64 → vision LLM parse
  3. Supplier gate: auto-match when confident; else choose existing/type/create
  4. staging.status = pending_review
  5. Send review message
  6. Store review_message_id in user context

On error: staging.status = error, user notified.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import time
import uuid
from collections.abc import Callable

from app.core.config import get_settings
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.user import User
from app.llm.item_parser import ParseError, ocr_page_to_markdown, parse_invoice, parse_price_list
from app.services.context_service import ContextService
from app.services.staging_service import StagingService
from app.services.supplier_service import SupplierService
from app.services.telemetry import record_llm_call, record_outgoing_message
from app.telegram.bot_api import (
    TelegramFileExpiredError,
    bind_current_session,
    bind_outgoing_db_logger,
    edit_message_text,
    get_file_bytes,
    send_message,
    send_message_with_keyboard,
)
from app.telegram.keyboards import (
    cb_confirm_upload,
    cb_delete_upload,
    cb_edit_row,
    cb_new_supplier,
    cb_pick_currency,
    cb_rev_page,
    cb_set_supplier,
    cb_type_supplier,
    make_button,
)
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(name="process_file_task")
def process_file_task(
    staging_id_hex: str,
    document_type: str,
    chat_id: int,
    progress_message_id: int | None = None,
) -> None:
    """Download, OCR, parse, and send review message for an uploaded file."""
    task_id = _get_task_id()
    settings = get_settings()
    doc_label = "invoice" if document_type == "invoice" else "price list"

    logger.info(
        "process_file_task start task_id=%s staging=%s doc_type=%s chat_id=%s progress_msg=%s",
        task_id, staging_id_hex, document_type, chat_id, progress_message_id,
    )

    try:
        staging_id = uuid.UUID(hex=staging_id_hex)
    except ValueError:
        logger.error("process_file_task: invalid staging_id_hex %s", staging_id_hex)
        return

    with worker_db_session() as db:
        staging = db.get(FileProcessingStaging, staging_id)
        if staging is None:
            logger.error("process_file_task: staging not found %s", staging_id)
            return

        staging_svc = StagingService(db)
        session_id = staging.session_id
        started_at = time.monotonic()
        last_progress_emit = 0.0

        def _fmt_duration(seconds: float) -> str:
            total = max(0, int(seconds))
            mins, secs = divmod(total, 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                return f"{hours}h {mins}m {secs}s"
            if mins > 0:
                return f"{mins}m {secs}s"
            return f"{secs}s"

        def _emit_progress(
            *,
            step: str,
            detail: str,
            current: int | None = None,
            total: int | None = None,
            force: bool = False,
        ) -> None:
            nonlocal last_progress_emit
            if progress_message_id is None:
                return

            now = time.monotonic()
            if (
                not force
                and now - last_progress_emit < 1.5
                and current is not None
                and total is not None
                and current < total
            ):
                return

            elapsed = now - started_at
            lines = [
                f"⏳ Processing your {doc_label}",
                "",
                f"Step: {step}",
                f"Status: {detail}",
                f"Elapsed: {_fmt_duration(elapsed)}",
            ]

            if current is not None and total is not None and total > 0:
                lines.append(f"Progress: {current}/{total}")
                if current > 0 and current < total:
                    eta = (elapsed / current) * (total - current)
                    lines.append(f"ETA: ~{_fmt_duration(eta)}")

            try:
                ok = edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text="\n".join(lines),
                    settings=settings,
                )
                if ok:
                    last_progress_emit = now
            except Exception:
                logger.exception(
                    "progress_update_failed staging=%s progress_msg=%s",
                    staging_id,
                    progress_message_id,
                )

        def _outgoing_db_logger(
            out_chat_id: int,
            out_text: str,
            telegram_message_id: int | None,
        ) -> None:
            if session_id is None or not out_text.strip():
                return
            try:
                with worker_db_session() as telemetry_db:
                    record_outgoing_message(
                        db=telemetry_db,
                        session_id=session_id,
                        chat_id=out_chat_id,
                        kind="reply",
                        text=out_text,
                        telegram_message_id=telegram_message_id,
                    )
                    telemetry_db.commit()
            except Exception:
                logger.exception(
                    "ocr_outgoing_log_failed staging=%s session_id=%s",
                    staging_id,
                    session_id,
                )

        def _on_llm_call(meta: dict[str, object]) -> None:
            if session_id is None:
                return
            model = str(meta.get("model") or "")
            if not model:
                return
            purpose = str(meta.get("purpose") or "ocr")

            usage_obj = meta.get("usage")
            usage = usage_obj if isinstance(usage_obj, dict) else None

            # OpenRouter: generation ID (gen-xxx) vs generic upstream ID
            or_gen_raw = meta.get("openrouter_generation_id")
            openrouter_generation_id = str(or_gen_raw) if or_gen_raw is not None else None
            upstream_raw = meta.get("upstream_id")
            upstream_id = str(upstream_raw) if upstream_raw is not None else None

            # Cost fields (OpenRouter only — None for other providers)
            cost_raw = meta.get("total_cost_usd")
            total_cost_usd = float(cost_raw) if isinstance(cost_raw, (int, float)) else None
            cache_raw = meta.get("cache_discount_usd")
            cache_discount_usd = float(cache_raw) if isinstance(cache_raw, (int, float)) else None
            upstream_cost_raw = meta.get("upstream_inference_cost_usd")
            upstream_inference_cost_usd = (
                float(upstream_cost_raw) if isinstance(upstream_cost_raw, (int, float)) else None
            )

            latency_raw = meta.get("latency_ms")
            latency_ms = int(latency_raw) if isinstance(latency_raw, int) else None

            error_raw = meta.get("error")
            error = str(error_raw) if error_raw is not None else None

            try:
                record_llm_call(
                    db=db,
                    session_id=session_id,
                    chat_id=chat_id,
                    purpose=purpose,
                    model=model,
                    openrouter_generation_id=openrouter_generation_id,
                    upstream_id=upstream_id,
                    usage=usage,
                    latency_ms=latency_ms,
                    total_cost_usd=total_cost_usd,
                    cache_discount_usd=cache_discount_usd,
                    upstream_inference_cost_usd=upstream_inference_cost_usd,
                    error=error,
                )
            except Exception:
                logger.exception(
                    "ocr_llm_telemetry_failed staging=%s session_id=%s",
                    staging_id,
                    session_id,
                )

        def _on_progress(meta: dict[str, object]) -> None:
            step_raw = meta.get("step")
            detail_raw = meta.get("detail")
            if not isinstance(step_raw, str) or not step_raw:
                return
            detail = str(detail_raw or "Working...")
            current = meta.get("current")
            total = meta.get("total")
            current_int = int(current) if isinstance(current, int) else None
            total_int = int(total) if isinstance(total, int) else None
            force_raw = meta.get("force")
            force = bool(force_raw) if isinstance(force_raw, bool) else False
            _emit_progress(
                step=step_raw,
                detail=detail,
                current=current_int,
                total=total_int,
                force=force,
            )

        with bind_current_session(session_id), bind_outgoing_db_logger(_outgoing_db_logger):
            try:
                # 1. Download file bytes
                _emit_progress(
                    step="Fetching file",
                    detail="Downloading your upload from Telegram...",
                    force=True,
                )
                file_bytes = get_file_bytes(file_id=staging.file_id, settings=settings)

                # 2. Extract structured data
                mime = staging.mime or "application/octet-stream"
                extracted = _extract(
                    file_bytes,
                    mime,
                    document_type,
                    settings,
                    on_llm_call=_on_llm_call,
                    on_progress=_on_progress,
                )

                if not extracted.get("line_items"):
                    raise ParseError("No line items could be extracted from this document")

                # 3. Supplier gate
                _emit_progress(
                    step="Matching supplier",
                    detail="Checking your supplier list for the best match...",
                    force=True,
                )
                supplier, sup_buttons = _resolve_supplier(
                    name=extracted.get("supplier"),
                    restaurant_id=staging.restaurant_id,
                    staging_id=staging_id,
                    db=db,
                )

                if supplier is not None:
                    staging.supplier_id = supplier.id
                    db.flush()

                # 4. Persist extracted data → status = pending_review
                staging_svc.set_extracted_data(staging_id, extracted)

                # 5. Build + send review message
                _emit_progress(
                    step="Preparing review",
                    detail="Finalizing extracted items and actions...",
                    force=True,
                )
                text = _build_review_text(staging, extracted, supplier, document_type)
                keyboard = _build_review_keyboard(staging, extracted, sup_buttons)
                msg_id = _send_with_keyboard(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                    settings=settings,
                )

                # 6. Store review_message_id in user context
                user = db.get(User, staging.uploaded_by)
                if user and msg_id:
                    ctx_svc = ContextService(db)
                    ctx_svc.set_fields(user, review_message_id=msg_id)

                db.commit()

                logger.info(
                    "process_file_task done staging=%s items=%d",
                    staging_id, len(extracted.get("line_items", [])),
                )

            except TelegramFileExpiredError as exc:
                logger.error("process_file_task file_expired staging=%s: %s", staging_id, exc)
                staging_svc.set_error(staging_id, f"file_expired: {exc}")
                db.commit()
                send_message(
                    chat_id=chat_id,
                    text="⚠️ The file is no longer available. Please upload again.",
                    settings=settings,
                )

            except ParseError as exc:
                logger.error("process_file_task parse_error staging=%s: %s", staging_id, exc)
                staging_svc.set_error(staging_id, str(exc))
                db.commit()
                send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Could not read document: {exc}",
                    settings=settings,
                )

            except Exception:
                logger.exception("process_file_task unexpected_error staging=%s", staging_id)
                staging_svc.set_error(staging_id, "unexpected error during processing")
                db.commit()
                send_message(
                    chat_id=chat_id,
                    text="⚠️ Processing failed. Please try uploading again.",
                    settings=settings,
                )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract(
    file_bytes: bytes,
    mime: str,
    document_type: str,
    settings,
    on_llm_call=None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict:
    """Route to PDF or image extraction based on mime type."""
    is_pdf = "pdf" in mime.lower()
    if is_pdf:
        return _extract_pdf(
            file_bytes,
            document_type,
            settings,
            on_llm_call=on_llm_call,
            on_progress=on_progress,
        )
    return _extract_image(
        file_bytes,
        document_type,
        settings,
        on_llm_call=on_llm_call,
        on_progress=on_progress,
    )


def _extract_pdf(
    file_bytes: bytes,
    document_type: str,
    settings,
    on_llm_call=None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict:
    """PDF: pdfplumber full text + camelot tables → combined LLM input.
    If < 5 items extracted, fall back to vision via pdf2image.
    """
    if on_progress is not None:
        on_progress(
            {
                "step": "Reading document",
                "detail": "Extracting text and tables from all pages...",
                "force": True,
            }
        )

    text_parts: list[str] = []
    table_parts: list[str] = []

    # pdfplumber — full text from all pages
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"[Page {page.page_number}]\n{page_text}")
    except Exception as exc:
        logger.warning("pdfplumber failed: %s", exc)

    # camelot — tabular data
    try:
        import camelot

        tables = camelot.read_pdf(  # type: ignore[attr-defined]
                io.BytesIO(file_bytes), pages="all", flavor="lattice"
            )
        for i, tbl in enumerate(tables):
            table_parts.append(f"[Table {i + 1}]\n{tbl.df.to_string()}")
    except Exception:
        try:
            import camelot

            tables = camelot.read_pdf(  # type: ignore[attr-defined]
                io.BytesIO(file_bytes), pages="all", flavor="stream"
            )
            for i, tbl in enumerate(tables):
                table_parts.append(f"[Table {i + 1}]\n{tbl.df.to_string()}")
        except Exception as exc2:
            logger.warning("camelot both flavors failed: %s", exc2)

    combined_parts = []
    if text_parts:
        combined_parts.append("FULL TEXT:\n" + "\n\n".join(text_parts))
    if table_parts:
        combined_parts.append("STRUCTURED TABLES:\n" + "\n\n".join(table_parts))

    if not combined_parts:
        raise ParseError("No text could be extracted from PDF")

    combined = "\n\n".join(combined_parts)

    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list
    if on_progress is not None:
        on_progress(
            {
                "step": "AI structuring",
                "detail": "Converting extracted text into structured items...",
                "force": True,
            }
        )
    result = parse_fn(settings, text=combined, on_llm_call=on_llm_call)

    if not (result.get("supplier") or "").strip():
        if on_progress is not None:
            on_progress(
                {
                    "step": "Supplier detection",
                    "detail": "Checking first page header to detect supplier...",
                    "force": True,
                }
            )
        header = _extract_pdf_header_vision(
            file_bytes,
            document_type,
            settings,
            on_llm_call=on_llm_call,
        )
        if header:
            for field in (
                "supplier",
                "supplier_contact_name",
                "supplier_phone",
                "supplier_email",
            ):
                if not result.get(field) and header.get(field):
                    result[field] = header[field]

    # Vision fallback if < 5 items
    if len(result.get("line_items", [])) < 5:
        logger.info(
            "pdf: only %d items from text path, trying vision fallback",
            len(result.get("line_items", [])),
        )
        if on_progress is not None:
            on_progress(
                {
                    "step": "Deep scan",
                    "detail": "Running detailed page-by-page AI scan for missing items...",
                    "force": True,
                }
            )
        try:
            vision_result = _extract_pdf_vision(
                file_bytes,
                document_type,
                settings,
                on_llm_call=on_llm_call,
                on_progress=on_progress,
            )
            if len(vision_result.get("line_items", [])) > len(result.get("line_items", [])):
                logger.info("pdf: vision fallback produced more items, using it")
                return vision_result
        except Exception as exc:
            logger.warning("pdf vision fallback failed: %s", exc)

    return result


def _resize_for_ocr(img, max_side: int = 4000):
    """Resize a PIL image so its larger side equals max_side (no upscaling)."""
    from PIL import Image as PILImage
    w, h = img.size
    scale = max_side / max(w, h)
    if scale >= 1:
        return img
    return img.resize((int(w * scale), int(h * scale)), PILImage.Resampling.LANCZOS)


def _extract_pdf_header_vision(
    file_bytes: bytes,
    document_type: str,
    settings,
    on_llm_call=None,
) -> dict | None:
    """Run a lightweight first-page vision pass to recover missing supplier header."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return None

    try:
        pages = convert_from_bytes(file_bytes, dpi=150, first_page=1, last_page=1)
    except Exception as exc:
        logger.warning("pdf_header_vision_convert_failed: %s", exc)
        return None

    if not pages:
        return None

    buf = io.BytesIO()
    _resize_for_ocr(pages[0]).save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    try:
        markdown = ocr_page_to_markdown(
            settings,
            b64,
            image_mime="image/jpeg",
            on_llm_call=on_llm_call,
        )
    except Exception as exc:
        logger.warning("pdf_header_vision_ocr_failed: %s", exc)
        return None

    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list
    try:
        parsed = parse_fn(settings, text=markdown, on_llm_call=on_llm_call)
    except Exception as exc:
        logger.warning("pdf_header_vision_parse_failed: %s", exc)
        return None

    return {k: v for k, v in parsed.items() if k != "line_items"}


def _extract_pdf_vision(
    file_bytes: bytes,
    document_type: str,
    settings,
    on_llm_call=None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict:
    """Convert PDF pages to images and parse via two-stage OCR → parse."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise ParseError("pdf2image not installed; cannot do vision fallback")

    images = convert_from_bytes(file_bytes, dpi=150)
    if not images:
        raise ParseError("PDF yielded no images")

    total_pages = len(images)
    if on_progress is not None:
        on_progress(
            {
                "step": "Deep scan",
                "detail": "Scanning PDF pages with AI...",
                "current": 0,
                "total": total_pages,
                "force": True,
            }
        )

    chunk_size = max(1, settings.vision_pdf_chunk_size or 3)
    all_items: list[dict] = []
    header: dict = {}

    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list

    for chunk_start in range(0, len(images), chunk_size):
        batch = images[chunk_start : chunk_start + chunk_size]

        # OCR every page in this batch, concatenate markdown, then parse once.
        page_markdowns: list[str] = []
        for page_offset, pil_img in enumerate(batch):
            resized = _resize_for_ocr(pil_img)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            try:
                md = ocr_page_to_markdown(
                    settings,
                    b64,
                    image_mime="image/jpeg",
                    on_llm_call=on_llm_call,
                )
                page_markdowns.append(md)
            except Exception as exc:
                logger.warning(
                    "pdf_vision: OCR failed for page %d: %s",
                    chunk_start + page_offset,
                    exc,
                )

        if not page_markdowns:
            continue

        combined_markdown = "\n\n---\n\n".join(page_markdowns)
        try:
            chunk_result = parse_fn(
                settings,
                text=combined_markdown,
                on_llm_call=on_llm_call,
            )
        except ParseError:
            continue

        # Keep header from first chunk that has it
        if not header.get("supplier") and chunk_result.get("supplier"):
            header = {k: v for k, v in chunk_result.items() if k != "line_items"}

        # Merge items; deduplicate by normalised name
        seen = {it.get("name", "").strip().lower() for it in all_items}
        for item in chunk_result.get("line_items", []):
            key = (item.get("name") or "").strip().lower()
            if key and key not in seen:
                all_items.append(item)
                seen.add(key)

        if on_progress is not None:
            done = min(chunk_start + len(batch), total_pages)
            on_progress(
                {
                    "step": "Deep scan",
                    "detail": "Scanning PDF pages with AI...",
                    "current": done,
                    "total": total_pages,
                }
            )

    header["line_items"] = all_items
    return header


def _extract_image(
    file_bytes: bytes,
    document_type: str,
    settings,
    on_llm_call=None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict:
    """Image: resize to 4000px max side → OCR markdown → JSON parse."""
    try:
        from PIL import Image as PILImage
    except ImportError:
        raise ParseError("Pillow not installed")

    img = PILImage.open(io.BytesIO(file_bytes))
    img = _resize_for_ocr(img)

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    if on_progress is not None:
        on_progress(
            {
                "step": "OCR extraction",
                "detail": "Reading text from your image...",
                "force": True,
            }
        )
    markdown = ocr_page_to_markdown(
        settings,
        b64,
        image_mime="image/jpeg",
        on_llm_call=on_llm_call,
    )

    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list
    if on_progress is not None:
        on_progress(
            {
                "step": "AI structuring",
                "detail": "Converting extracted text into structured items...",
                "force": True,
            }
        )
    return parse_fn(settings, text=markdown, on_llm_call=on_llm_call)


# ---------------------------------------------------------------------------
# Supplier resolution gate
# ---------------------------------------------------------------------------

def _supplier_candidates_for_name(
    *,
    svc: SupplierService,
    name: str | None,
    restaurant_id: uuid.UUID,
) -> list[tuple[object, float]]:
    """Return ranked restaurant-scoped supplier candidates for a query."""
    query = (name or "").strip()
    if not query:
        suppliers = svc.list_for_restaurant(restaurant_id=restaurant_id, offset=0, limit=5)
        return [(sup, 1.0) for sup in suppliers]

    strict = svc.fuzzy_search_for_restaurant(
        name=query,
        restaurant_id=restaurant_id,
        threshold=0.5,
    )
    if strict:
        return strict

    relaxed = svc.fuzzy_search_for_restaurant(
        name=query,
        restaurant_id=restaurant_id,
        threshold=0.2,
    )
    if relaxed:
        return relaxed

    # Final fallback: return ranked restaurant suppliers so user can still choose.
    return svc.fuzzy_search_for_restaurant(
        name=query,
        restaurant_id=restaurant_id,
        threshold=0.0,
    )


def _resolve_supplier(
    name: str | None,
    restaurant_id: uuid.UUID,
    staging_id: uuid.UUID,
    db,
) -> tuple[object | None, list[dict] | None]:
    """Return (matched_supplier, extra_buttons_or_None).

    Auto-match at high confidence; otherwise offer:
    - choose existing supplier
    - type supplier name
    - create new supplier
    """
    svc = SupplierService(db)
    query = (name or "").strip()
    matches = _supplier_candidates_for_name(
        svc=svc,
        name=query,
        restaurant_id=restaurant_id,
    )

    if query and matches and matches[0][1] >= 0.8:
        best = matches[0][0]
        return best, None  # auto-resolved

    buttons: list[dict] = []
    for rank, (sup, _score) in enumerate(matches[:3]):
        buttons.append(make_button(f"✅ {sup.name}", cb_set_supplier(staging_id, rank)))

    buttons.append(make_button("⌨️ Type Supplier Name", cb_type_supplier(staging_id)))
    create_label = f"➕ Create '{query}'" if query else "➕ Add Supplier"
    buttons.append(make_button(create_label, cb_new_supplier(staging_id)))
    return None, buttons


# ---------------------------------------------------------------------------
# Review message builders
# ---------------------------------------------------------------------------

_REVIEW_PAGE_SIZE = 8  # items visible per page in the review message


def _build_review_text(
    staging: FileProcessingStaging,
    extracted: dict,
    auto_supplier,
    document_type: str,
    page: int = 0,
) -> str:
    """Build the review message text for the given page (0-indexed)."""
    items = extracted.get("line_items", [])
    total = len(items)
    total_pages = max(1, math.ceil(total / _REVIEW_PAGE_SIZE))
    start = page * _REVIEW_PAGE_SIZE
    end = min(start + _REVIEW_PAGE_SIZE, total)

    lines: list[str] = []

    # Header
    supplier_name = (auto_supplier.name if auto_supplier else None) or extracted.get("supplier") or "Unknown Supplier"
    sup_label = f"{supplier_name} ✅" if auto_supplier else supplier_name
    doc_emoji = "📄" if document_type == "invoice" else "📋"
    doc_label = "Invoice" if document_type == "invoice" else "Price List"
    lines.append(f"{doc_emoji} {doc_label} — {sup_label}")

    # Meta line
    meta: list[str] = []
    if document_type == "invoice":
        if extracted.get("invoice_date"):
            meta.append(f"Date: {extracted['invoice_date']}")
        if extracted.get("invoice_number"):
            meta.append(f"#{extracted['invoice_number']}")
    else:
        if extracted.get("effective_date"):
            meta.append(f"Effective: {extracted['effective_date']}")
        if extracted.get("lead_time"):
            meta.append(f"Lead: {extracted['lead_time']}")
    currency = extracted.get("currency")
    if currency:
        meta.append(currency)
    elif document_type == "price_list":
        meta.append("💱 ?currency")
    if meta:
        lines.append("  ".join(meta))

    # Page indicator (only when there are multiple pages)
    if total_pages > 1:
        lines.append(f"Items {start + 1}–{end} of {total}")

    lines.append("")

    # Items for the current page (1-indexed display uses absolute position)
    for i, item in enumerate(items[start:end]):
        abs_i = start + i
        name = item.get("name", "?")
        unit = item.get("unit") or ""
        price = item.get("unit_price")
        price_str = f"${price:.2f}" if price is not None else "⚠️?price"

        if document_type == "invoice":
            qty = item.get("qty")
            qty_str = f"{qty}" if qty is not None else "⚠️?"
            amount = item.get("amount")
            row = f"{abs_i + 1}. {name}   {qty_str}{unit} × {price_str}"
            if amount:
                row += f" = ${amount:.2f}"
                if price is not None and qty is not None and abs(qty * price - amount) > 0.01:
                    row += " ⚠️"
        else:
            row = f"{abs_i + 1}. {name}   {unit} @ {price_str}"

        lines.append(row)

    warnings: list[str] = []
    if not auto_supplier:
        warnings.append("⚠️ Supplier not confirmed — please set before confirming.")
    if document_type == "price_list" and not extracted.get("currency"):
        warnings.append("⚠️ Currency not set — required before confirming.")
    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines)


def _build_review_keyboard(
    staging: FileProcessingStaging,
    extracted: dict,
    sup_buttons: list[dict] | None,
    page: int = 0,
) -> dict:
    """Build the inline keyboard for the review message (paginated)."""
    staging_id = staging.id
    items = extracted.get("line_items", [])
    total = len(items)
    total_pages = max(1, math.ceil(total / _REVIEW_PAGE_SIZE))
    start = page * _REVIEW_PAGE_SIZE
    end = min(start + _REVIEW_PAGE_SIZE, total)

    keyboard: list[list[dict]] = []

    # Row 1: Confirm + Cancel
    keyboard.append([
        make_button("✅ Confirm All", cb_confirm_upload(staging_id)),
        make_button("❌ Cancel", cb_delete_upload(staging_id)),
    ])

    # Supplier buttons if needed
    if sup_buttons:
        for btn in sup_buttons:
            keyboard.append([btn])

    # Currency button for price lists when currency is not yet set
    if staging.document_type == "price_list" and not extracted.get("currency"):
        keyboard.append([make_button("💱 Set Currency", cb_pick_currency(staging_id))])

    # Navigation row (only when multiple pages exist)
    if total_pages > 1:
        nav_row: list[dict] = []
        if page > 0:
            nav_row.append(make_button(f"← {page}/{total_pages}", cb_rev_page(staging_id, page - 1)))
        if page < total_pages - 1:
            nav_row.append(make_button(f"{page + 2}/{total_pages} →", cb_rev_page(staging_id, page + 1)))
        if nav_row:
            keyboard.append(nav_row)

    # Edit buttons for every item on the current page (4 per row)
    edit_buttons = [
        make_button(f"✏️ #{start + i + 1}", cb_edit_row(staging_id, start + i))
        for i in range(end - start)
    ]
    for i in range(0, len(edit_buttons), 4):
        keyboard.append(edit_buttons[i : i + 4])

    return {"inline_keyboard": keyboard}


# ---------------------------------------------------------------------------
# Bot API helper with keyboard support
# ---------------------------------------------------------------------------

def _send_with_keyboard(
    chat_id: int,
    text: str,
    reply_markup: dict,
    settings,
) -> int | None:
    """Send a message with an inline keyboard. Returns message_id or None."""
    return send_message_with_keyboard(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        settings=settings,
    )
