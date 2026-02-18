"""
Celery task: OCR + LLM extraction for uploaded files.

process_file_task(staging_id_hex, document_type, chat_id):
  1. Download file from Telegram
  2. PDF → pdfplumber + camelot combined text → LLM parse
            < 5 items → pdf2image vision fallback
     Image → base64 → vision LLM parse
  3. Supplier gate: ≥0.8 auto-match | 0.5–0.8 suggest | <0.5 new
  4. staging.status = pending_review
  5. Send review message
  6. Store review_message_id in user context

On error: staging.status = error, user notified.
"""
from __future__ import annotations

import base64
import io
import logging
import uuid

from app.core.config import get_settings
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.user import User
from app.llm.item_parser import ParseError, parse_invoice, parse_price_list
from app.services.context_service import ContextService
from app.services.staging_service import StagingService
from app.services.supplier_service import SupplierService
from app.telegram.bot_api import (
    TelegramFileExpiredError,
    get_file_bytes,
    send_message,
)
from app.telegram.keyboards import (
    cb_confirm_upload,
    cb_delete_upload,
    cb_edit_row,
    cb_new_supplier,
    cb_set_supplier,
    make_button,
    uuid_to_hex,
)
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(name="process_file_task")
def process_file_task(staging_id_hex: str, document_type: str, chat_id: int) -> None:
    """Download, OCR, parse, and send review message for an uploaded file."""
    task_id = _get_task_id()
    settings = get_settings()

    logger.info(
        "process_file_task start task_id=%s staging=%s doc_type=%s chat_id=%s",
        task_id, staging_id_hex, document_type, chat_id,
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

        try:
            # 1. Download file bytes
            file_bytes = get_file_bytes(file_id=staging.file_id, settings=settings)

            # 2. Extract structured data
            mime = staging.mime or "application/octet-stream"
            extracted = _extract(file_bytes, mime, document_type, settings)

            if not extracted.get("line_items"):
                raise ParseError("No line items could be extracted from this document")

            # 3. Supplier gate
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
            text = _build_review_text(staging, extracted, supplier, document_type)
            keyboard = _build_review_keyboard(staging, extracted, sup_buttons)

            from app.telegram.bot_api import send_message as _send  # local import to ease mocking

            # We need to send with reply_markup; use httpx directly since send_message
            # doesn't support inline keyboards yet in the existing bot_api. Wire via raw call.
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

def _extract(file_bytes: bytes, mime: str, document_type: str, settings) -> dict:
    """Route to PDF or image extraction based on mime type."""
    is_pdf = "pdf" in mime.lower()
    if is_pdf:
        return _extract_pdf(file_bytes, document_type, settings)
    return _extract_image(file_bytes, mime, document_type, settings)


def _extract_pdf(file_bytes: bytes, document_type: str, settings) -> dict:
    """PDF: pdfplumber full text + camelot tables → combined LLM input.
    If < 5 items extracted, fall back to vision via pdf2image.
    """
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

        tables = camelot.read_pdf(
            io.BytesIO(file_bytes), pages="all", flavor="lattice"
        )
        for i, tbl in enumerate(tables):
            table_parts.append(f"[Table {i + 1}]\n{tbl.df.to_string()}")
    except Exception:
        try:
            import camelot

            tables = camelot.read_pdf(
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
    result = parse_fn(settings, text=combined)

    # Vision fallback if < 5 items
    if len(result.get("line_items", [])) < 5:
        logger.info(
            "pdf: only %d items from text path, trying vision fallback",
            len(result.get("line_items", [])),
        )
        try:
            vision_result = _extract_pdf_vision(file_bytes, document_type, settings)
            if len(vision_result.get("line_items", [])) > len(result.get("line_items", [])):
                logger.info("pdf: vision fallback produced more items, using it")
                return vision_result
        except Exception as exc:
            logger.warning("pdf vision fallback failed: %s", exc)

    return result


def _extract_pdf_vision(file_bytes: bytes, document_type: str, settings) -> dict:
    """Convert PDF pages to images and parse via vision model."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise ParseError("pdf2image not installed; cannot do vision fallback")

    images = convert_from_bytes(file_bytes, dpi=150)
    if not images:
        raise ParseError("PDF yielded no images")

    chunk_size = max(1, settings.vision_pdf_chunk_size or 3)
    all_items: list[dict] = []
    header: dict = {}

    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list

    for chunk_start in range(0, len(images), chunk_size):
        batch = images[chunk_start : chunk_start + chunk_size]
        # Use first image of each batch for now (simplest, avoids multi-image API variance)
        img = batch[0]
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        try:
            chunk_result = parse_fn(settings, image_b64=b64, image_mime="image/jpeg")
        except ParseError:
            continue

        # Keep header from first chunk that has it
        if not header.get("supplier") and chunk_result.get("supplier"):
            header = {k: v for k, v in chunk_result.items() if k != "line_items"}

        # Merge items; deduplicate by name
        seen = {it.get("name") for it in all_items}
        for item in chunk_result.get("line_items", []):
            if item.get("name") not in seen:
                all_items.append(item)
                seen.add(item.get("name"))

    header["line_items"] = all_items
    return header


def _extract_image(file_bytes: bytes, mime: str, document_type: str, settings) -> dict:
    """Image: base64-encode and call vision model."""
    b64 = base64.b64encode(file_bytes).decode()
    image_mime = mime if mime.startswith("image/") else "image/jpeg"
    parse_fn = parse_invoice if document_type == "invoice" else parse_price_list
    return parse_fn(settings, image_b64=b64, image_mime=image_mime)


# ---------------------------------------------------------------------------
# Supplier resolution gate
# ---------------------------------------------------------------------------

def _resolve_supplier(
    name: str | None,
    restaurant_id: uuid.UUID,
    staging_id: uuid.UUID,
    db,
) -> tuple[object | None, list[dict] | None]:
    """Return (matched_supplier, extra_buttons_or_None).

    ≥ 0.8 → auto-match; no buttons (None).
    0.5–0.8 → suggest best matches + "Create new" button.
    < 0.5 → "Create new" button only.
    """
    if not name:
        return None, [
            make_button("➕ Add Supplier", cb_new_supplier(staging_id))
        ]

    svc = SupplierService(db)
    matches = svc.fuzzy_search_for_restaurant(
        name=name,
        restaurant_id=restaurant_id,
        threshold=0.5,
    )

    if not matches:
        return None, [
            make_button(f"➕ Create '{name}'", cb_new_supplier(staging_id))
        ]

    best, score = matches[0]

    if score >= 0.8:
        return best, None  # auto-resolved

    # 0.5–0.8: offer top matches + new
    buttons: list[dict] = []
    for rank, (sup, _s) in enumerate(matches[:2]):
        buttons.append(make_button(f"✅ {sup.name}", cb_set_supplier(staging_id, rank)))
    buttons.append(make_button(f"➕ Create '{name}'", cb_new_supplier(staging_id)))
    return None, buttons


# ---------------------------------------------------------------------------
# Review message builders
# ---------------------------------------------------------------------------

def _build_review_text(
    staging: FileProcessingStaging,
    extracted: dict,
    auto_supplier,
    document_type: str,
) -> str:
    """Build the review message text."""
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
    if extracted.get("currency"):
        meta.append(extracted["currency"])
    if meta:
        lines.append("  ".join(meta))

    lines.append("")

    # Line items
    items = extracted.get("line_items", [])
    for i, item in enumerate(items[:10]):
        name = item.get("name", "?")
        if document_type == "invoice":
            qty = item.get("qty", 0)
            unit = item.get("unit") or ""
            price = item.get("unit_price", 0)
            amount = item.get("amount")
            row = f"{i + 1}. {name}   {qty}{unit} × ${price:.2f}"
            if amount:
                row += f" = ${amount:.2f}"
                if abs(qty * price - amount) > 0.01:
                    row += " ⚠️"
            lines.append(row)
        else:
            unit = item.get("unit") or ""
            price = item.get("unit_price", 0)
            lines.append(f"{i + 1}. {name}   {unit} @ ${price:.2f}")

    if len(items) > 10:
        lines.append(f"… +{len(items) - 10} more items")

    if not auto_supplier:
        lines.append("")
        lines.append("⚠️ Supplier not confirmed — please set before confirming.")

    return "\n".join(lines)


def _build_review_keyboard(
    staging: FileProcessingStaging,
    extracted: dict,
    sup_buttons: list[dict] | None,
) -> dict:
    """Build the inline keyboard for the review message."""
    staging_id = staging.id
    items = extracted.get("line_items", [])
    item_count = len(items)

    keyboard: list[list[dict]] = []

    # Row 1: Confirm + Cancel
    keyboard.append([
        make_button("✅ Confirm All", cb_confirm_upload(staging_id)),
        make_button("❌ Cancel", cb_delete_upload(staging_id)),
    ])

    # Row 2+: Supplier buttons if needed
    if sup_buttons:
        for btn in sup_buttons:
            keyboard.append([btn])

    # Edit buttons (max 8 items, 4 per row)
    if item_count > 0:
        edit_buttons = [
            make_button(f"✏️ #{i + 1}", cb_edit_row(staging_id, i))
            for i in range(min(item_count, 8))
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
    import httpx

    if not settings.telegram_bot_token:
        # Dev mode: fall back to plain send_message
        logger.warning("send_with_keyboard: no bot token — falling back to plain send")
        return send_message(chat_id=chat_id, text=text, settings=settings)

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": reply_markup,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            msg_id = (data.get("result") or {}).get("message_id")
            return int(msg_id) if msg_id else None
        logger.error("send_with_keyboard failed: %s", data.get("description"))
        return None
    except Exception as exc:
        logger.error("send_with_keyboard error: %s", exc)
        return None
