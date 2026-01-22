from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.documents import Documents
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_steps import FileProcessingSteps
from app.db.models.processing_events import ProcessingEvents
from app.db.models.suppliers import Suppliers
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.user import User
from app.conversation.context import load_context, save_context
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.vision_client import VisionCallResult, _get_vision_settings
from app.processing.file_processor import (
    extract_inventory_data,
    extract_invoice_data,
    extract_price_list_data,
)
from app.telegram.bot_api import get_file_bytes, send_message
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.telemetry import (
    record_llm_call,
    record_outgoing_message,
    schedule_openrouter_cost_backfill,
)
from app.workers.utils import _get_task_id, _parse_uuid

logger = logging.getLogger(__name__)


def _record_vision_telemetry(
    session_uuid: uuid.UUID | None,
    chat_id: int,
    telemetry_results: list,
    purpose_base: str,
    processing_type: str,
) -> None:
    """
    Record telemetry for vision model calls using a FRESH database session.

    This function creates its own session to avoid idle-in-transaction timeout
    issues that occur when the main session sits idle during long LLM processing.

    Args:
        session_uuid: Session UUID (if None, logs warning and skips)
        chat_id: Chat ID
        telemetry_results: List of VisionCallResult objects
        purpose_base: Base purpose name (e.g., "vision_invoice")
        processing_type: Processing type (invoice/price_list/inventory)
    """
    if not session_uuid:
        logger.warning(
            "vision_telemetry_skipped_no_session",
            extra={"chat_id": chat_id, "purpose_base": purpose_base},
        )
        return

    # Use a fresh session for telemetry to avoid idle-in-transaction timeout
    with worker_db_session() as db:
        for idx, result in enumerate(telemetry_results):
            # Determine purpose with page index if applicable
            if len(telemetry_results) > 1:
                purpose = f"{purpose_base}_page_{idx + 1}"
            else:
                purpose = f"{purpose_base}_page"

            # Build metadata with page index
            generation_json = {}
            if result.response_data:
                generation_json = result.response_data.copy()
            if len(telemetry_results) > 1:
                generation_json["page_index"] = idx + 1
            generation_json["processing_type"] = processing_type

            try:
                llm_call_id = record_llm_call(
                    db=db,
                    session_id=session_uuid,
                    chat_id=chat_id,
                    purpose=purpose,
                    model=result.model,
                    openrouter_generation_id=result.openrouter_generation_id,
                    upstream_id=result.response_data.get("id")
                    if isinstance(result.response_data.get("id"), str)
                    else None,
                    provider_name=result.response_data.get("provider")
                    if isinstance(result.response_data.get("provider"), str)
                    else None,
                    usage=result.usage,
                    latency_ms=result.latency_ms,
                    openrouter_generation_json=generation_json if generation_json else None,
                    error=result.error,
                )
                db.commit()

                # Schedule cost backfill if generation ID exists
                if result.openrouter_generation_id:
                    try:
                        schedule_openrouter_cost_backfill(
                            llm_call_id=llm_call_id, delay_seconds=120
                        )
                    except Exception:
                        logger.exception(
                            "vision_cost_backfill_schedule_failed",
                            extra={
                                "llm_call_id": str(llm_call_id),
                                "generation_id": result.openrouter_generation_id,
                            },
                        )
            except Exception:
                logger.exception(
                    "vision_telemetry_record_failed",
                    extra={
                        "purpose": purpose,
                        "model": result.model,
                        "error": result.error,
                    },
                )


def _record_file_processing_steps(
    run_id: uuid.UUID,
    telemetry_results: list[VisionCallResult],
    stage: str,
) -> None:
    """Persist file processing step inputs/outputs using a fresh session."""
    if not telemetry_results:
        return

    with worker_db_session() as db:
        for idx, result in enumerate(telemetry_results, start=1):
            step = FileProcessingSteps(
                run_id=run_id,
                stage=stage,
                status="failed" if result.error else "completed",
                step_index=idx,
                input_text=result.input_text,
                prompt_text=result.prompt_text,
                output_text=result.content or None,
                request_json=result.request_payload,
                response_json=result.response_data or None,
                usage_json=result.usage,
                model=result.model,
                latency_ms=result.latency_ms,
                error_message=result.error,
            )
            db.add(step)
        db.commit()


def _set_pending_file_processing_action(
    *,
    db: Session,
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    staging_id: uuid.UUID,
    field: str,
    supplier_id: uuid.UUID | None = None,
) -> None:
    user = db.get(User, user_id)
    if not user:
        return

    context = load_context(db, user)
    context.pending_action = {
        "type": "file_processing_missing_field",
        "staging_id": str(staging_id),
        "field": field,
    }
    context.active_restaurant_id = str(restaurant_id)
    if supplier_id:
        context.active_supplier_id = str(supplier_id)
    save_context(db, user, context)


def _clear_pending_file_processing_action(
    *,
    db: Session,
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    supplier_id: uuid.UUID | None = None,
) -> None:
    user = db.get(User, user_id)
    if not user:
        return

    context = load_context(db, user)
    if (
        context.pending_action
        and context.pending_action.get("type") == "file_processing_missing_field"
    ):
        context.pending_action = None
    context.active_restaurant_id = str(restaurant_id)
    if supplier_id:
        context.active_supplier_id = str(supplier_id)
    save_context(db, user, context)


def _send_message_with_telemetry(
    *,
    db: Session | None,
    chat_id: int,
    text: str,
    settings: Settings,
    session_uuid: uuid.UUID | None,
    kind: str = "reply",
) -> None:
    telegram_message_id = send_message(chat_id=chat_id, text=text, settings=settings)
    if not session_uuid or db is None:
        return
    try:
        record_outgoing_message(
            db=db,
            session_id=session_uuid,
            chat_id=chat_id,
            kind=kind,
            text=text,
            telegram_message_id=telegram_message_id,
            llm_call_id=None,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "outgoing_message_record_failed",
            extra={
                "chat_id": chat_id,
                "session_id": str(session_uuid),
                "kind": kind,
            },
        )


def _set_pending_file_processing_confirm_action(
    *,
    db: Session,
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    staging_id: uuid.UUID,
    supplier_id: uuid.UUID | None = None,
) -> None:
    user = db.get(User, user_id)
    if not user:
        return

    context = load_context(db, user)
    context.pending_action = {
        "type": "file_processing_confirm",
        "staging_id": str(staging_id),
    }
    context.active_restaurant_id = str(restaurant_id)
    if supplier_id:
        context.active_supplier_id = str(supplier_id)
    save_context(db, user, context)


@celery_app.task(name="process_invoice_file_task")
def process_invoice_file_task(
    *,
    restaurant_id: str,
    file_id: str,
    supplier_id: str | None = None,
    chat_id: int,
    user_id: str,
    session_id: str | None = None,
) -> None:
    """Process an invoice file: extract data, match products, store in staging."""
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=process_invoice_file_task task_id=%s restaurant_id=%s file_id=%s",
        task_id,
        restaurant_id,
        file_id,
    )

    settings = get_settings()
    restaurant_uuid = _parse_uuid(restaurant_id)
    user_uuid = _parse_uuid(user_id)
    session_uuid = _parse_uuid(session_id) if session_id else None
    db_session: Session | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = (
                telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            )
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            run = FileProcessingRuns(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=None,
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                processing_type="invoice",
                status="processing",
                pages_total=None,
                pages_processed=0,
                current_stage="processing",
                error_message=None,
                started_at=now,
            )
            db.add(run)
            db.flush()

            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                run_id=run.id,
                document_id=None,
                processing_type="invoice",
                extracted_data_json={},
                product_alias_matches_json={},
                status="processing",
                pages_total=None,
                pages_processed=0,
                started_at=now,
            )
            db.add(staging)
            db.commit()

            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            # Extract invoice data (with DB-aware extraction)
            try:
                extracted_data = extract_invoice_data(file_bytes, mime_type, settings, db, filename)

                # Record telemetry for vision calls (uses fresh session internally)
                telemetry_results = extracted_data.pop("_telemetry_results", [])
                if telemetry_results:
                    _record_vision_telemetry(
                        session_uuid=session_uuid,
                        chat_id=chat_id,
                        telemetry_results=telemetry_results,
                        purpose_base="vision_invoice",
                        processing_type="invoice",
                    )
                    _record_file_processing_steps(
                        run_id=run.id,
                        telemetry_results=telemetry_results,
                        stage="vision_invoice",
                    )
            except Exception as exc:
                failed_at = dt.datetime.now(dt.UTC)
                run.status = "failed"
                run.current_stage = "failed"
                run.error_message = str(exc)
                run.finished_at = failed_at
                staging.status = "failed"
                staging.error_message = str(exc)
                staging.finished_at = failed_at
                db.commit()
                # Record error telemetry if possible (uses fresh session internally)
                if session_uuid:
                    try:
                        model, _, _ = _get_vision_settings(settings)
                        error_result = VisionCallResult(
                            content="",
                            model=model,
                            latency_ms=0,
                            usage=None,
                            openrouter_generation_id=None,
                            response_headers={},
                            response_data={},
                            error=f"Invoice extraction failed: {exc}",
                        )
                        _record_vision_telemetry(
                            session_uuid=session_uuid,
                            chat_id=chat_id,
                            telemetry_results=[error_result],
                            purpose_base="vision_invoice",
                            processing_type="invoice",
                        )
                        _record_file_processing_steps(
                            run_id=run.id,
                            telemetry_results=[error_result],
                            stage="vision_invoice_error",
                        )
                    except Exception:
                        logger.exception("failed_to_record_error_telemetry")
                raise

            # Check for missing required fields
            supplier_name = extracted_data.get("supplier_name", "").strip() if extracted_data.get("supplier_name") else ""
            currency = extracted_data.get("currency", "").strip() if extracted_data.get("currency") else ""

            # Find or create supplier if not provided
            supplier_uuid = None
            if supplier_id:
                supplier_uuid = _parse_uuid(supplier_id)
            elif supplier_name:
                supplier = db.scalar(
                    select(Suppliers).where(
                        Suppliers.restaurant_id == restaurant_uuid,
                        Suppliers.name.ilike(supplier_name),
                        Suppliers.is_active == True,
                    )
                )
                if supplier:
                    supplier_uuid = supplier.id

            # No longer matching product aliases - we store raw names and search when user asks
            alias_matches = {}

            # Create document record
            document = Documents(
                restaurant_id=restaurant_uuid,
                supplier_id=supplier_uuid,
                doc_type="invoice",
                file_url=file_id,
                uploaded_at=dt.datetime.now(dt.UTC),
            )
            db.add(document)
            db.flush()

            # Determine status based on missing fields
            status = "pending_review"
            if not supplier_name:
                status = "awaiting_supplier"
            elif not currency:
                status = "awaiting_currency"

            line_items = extracted_data.get("line_items", [])
            page_numbers = {
                item.get("source_page")
                for item in line_items
                if isinstance(item.get("source_page"), int)
            }
            pages_processed = len(page_numbers)
            pages_total = max(page_numbers) if page_numbers else None
            finished_at = dt.datetime.now(dt.UTC)

            run.document_id = document.id
            run.status = "completed"
            run.current_stage = status
            run.pages_total = pages_total
            run.pages_processed = pages_processed
            run.finished_at = finished_at

            staging.document_id = document.id
            staging.extracted_data_json = extracted_data
            staging.product_alias_matches_json = alias_matches
            staging.status = status
            staging.pages_total = pages_total
            staging.pages_processed = pages_processed
            staging.finished_at = finished_at
            staging.error_message = None
            db.commit()

            # Send message based on status
            if status == "awaiting_supplier":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    field="supplier",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text="I couldn't find the supplier in this invoice. What supplier is it from?",
                    settings=settings,
                    session_uuid=session_uuid,
                )
            elif status == "awaiting_currency":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    field="currency",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text="What currency is this invoice in? (e.g., USD, EUR)",
                    settings=settings,
                    session_uuid=session_uuid,
                )
            else:
                _clear_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _set_pending_file_processing_confirm_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    supplier_id=supplier_uuid,
                )
                db.commit()
                # Format and send preview message with summary
                line_items = extracted_data.get("line_items", [])
                total_items = len(line_items)
                
                lines = ["📄 **Invoice Extracted**\n"]
                lines.append(f"**Supplier:** {supplier_name}")
                
                # Show contact info if available
                contact_name = extracted_data.get("contact_name")
                contact_email = extracted_data.get("contact_email")
                contact_phone = extracted_data.get("contact_phone")
                if contact_name:
                    lines.append(f"**Contact:** {contact_name}")
                if contact_email:
                    lines.append(f"**Email:** {contact_email}")
                if contact_phone:
                    lines.append(f"**Phone:** {contact_phone}")
                
                lines.append(f"**Invoice #:** {extracted_data.get('invoice_number', 'N/A')}")
                lines.append(f"**Date:** {extracted_data.get('invoice_date', 'N/A')}")
                due_date = extracted_data.get('due_date')
                if due_date:
                    lines.append(f"**Due Date:** {due_date}")
                lines.append(f"**Currency:** {currency}")
                lines.append(f"**Total:** {currency} {extracted_data.get('total', 'N/A')}")
                lines.append(f"**Line Items:** {total_items}")
                
                # Show sample of line items (first 10 for invoices)
                sample_size = min(10, total_items)
                if sample_size > 0:
                    lines.append(f"\n**Line Items ({sample_size} of {total_items}):**")
                    for i, item in enumerate(line_items[:sample_size], 1):
                        desc = item.get("description_raw", item.get("description", "N/A"))
                        qty = item.get("quantity", "N/A")
                        unit = item.get("unit", "")
                        total = item.get("line_total", "N/A")
                        lines.append(f"  {i}. {desc} - {qty} {unit} = {currency} {total}")
                    
                    if total_items > sample_size:
                        lines.append(f"  ... and {total_items - sample_size} more items")
                
                lines.append("\n---")
                lines.append("✅ Say **/confirm** to save this invoice")
                lines.append("❓ Ask to see all line items if needed")
                lines.append("✏️ Tell me if anything needs correcting")
                
                preview_text = "\n".join(lines)
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text=preview_text,
                    settings=settings,
                    session_uuid=session_uuid,
                )

            # Record processing event
            db.add(
                ProcessingEvents(
                    session_id=session_uuid if session_uuid else staging.id,
                    at=dt.datetime.now(dt.UTC),
                    event="invoice_file_processed_v0",
                    payload_json=json.dumps(
                        {"staging_id": str(staging.id), "file_id": file_id}, ensure_ascii=False
                    ),
                    error=None,
                )
            )
            db.commit()

    except Exception as exc:
        logger.exception(
            "process_invoice_file_task_failed",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        # Send error message to user
        try:
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your invoice file. Error: {str(exc)}",
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise


@celery_app.task(name="process_price_list_file_task")
def process_price_list_file_task(
    *,
    restaurant_id: str,
    file_id: str,
    supplier_id: str | None = None,
    chat_id: int,
    user_id: str,
    session_id: str | None = None,
) -> None:
    """Process a price list file: extract data, match products, store in staging."""
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=process_price_list_file_task task_id=%s restaurant_id=%s file_id=%s",
        task_id,
        restaurant_id,
        file_id,
    )

    settings = get_settings()
    restaurant_uuid = _parse_uuid(restaurant_id)
    user_uuid = _parse_uuid(user_id)
    session_uuid = _parse_uuid(session_id) if session_id else None
    db_session: Session | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = (
                telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            )
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            run = FileProcessingRuns(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=None,
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                processing_type="price_list",
                status="processing",
                pages_total=None,
                pages_processed=0,
                current_stage="processing",
                error_message=None,
                started_at=now,
            )
            db.add(run)
            db.flush()

            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                run_id=run.id,
                document_id=None,
                processing_type="price_list",
                extracted_data_json={},
                product_alias_matches_json={},
                status="processing",
                pages_total=None,
                pages_processed=0,
                started_at=now,
            )
            db.add(staging)
            db.commit()

            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            # Extract price list data (with DB-aware extraction)
            try:
                extracted_data = extract_price_list_data(file_bytes, mime_type, settings, db, filename)

                # Record telemetry for vision calls (uses fresh session internally)
                telemetry_results = extracted_data.pop("_telemetry_results", [])
                if telemetry_results:
                    _record_vision_telemetry(
                        session_uuid=session_uuid,
                        chat_id=chat_id,
                        telemetry_results=telemetry_results,
                        purpose_base="vision_price_list",
                        processing_type="price_list",
                    )
                    _record_file_processing_steps(
                        run_id=run.id,
                        telemetry_results=telemetry_results,
                        stage="vision_price_list",
                    )
            except Exception as exc:
                failed_at = dt.datetime.now(dt.UTC)
                run.status = "failed"
                run.current_stage = "failed"
                run.error_message = str(exc)
                run.finished_at = failed_at
                staging.status = "failed"
                staging.error_message = str(exc)
                staging.finished_at = failed_at
                db.commit()
                # Record error telemetry if possible (uses fresh session internally)
                if session_uuid:
                    try:
                        model, _, _ = _get_vision_settings(settings)
                        error_result = VisionCallResult(
                            content="",
                            model=model,
                            latency_ms=0,
                            usage=None,
                            openrouter_generation_id=None,
                            response_headers={},
                            response_data={},
                            error=f"Price list extraction failed: {exc}",
                        )
                        _record_vision_telemetry(
                            session_uuid=session_uuid,
                            chat_id=chat_id,
                            telemetry_results=[error_result],
                            purpose_base="vision_price_list",
                            processing_type="price_list",
                        )
                        _record_file_processing_steps(
                            run_id=run.id,
                            telemetry_results=[error_result],
                            stage="vision_price_list_error",
                        )
                    except Exception:
                        logger.exception("failed_to_record_error_telemetry")
                raise

            # Check for missing required fields
            supplier_name = extracted_data.get("supplier_name", "").strip() if extracted_data.get("supplier_name") else ""
            currency = extracted_data.get("currency", "").strip() if extracted_data.get("currency") else ""

            # Find or create supplier if not provided
            supplier_uuid = None
            if supplier_id:
                supplier_uuid = _parse_uuid(supplier_id)
            elif supplier_name:
                supplier = db.scalar(
                    select(Suppliers).where(
                        Suppliers.restaurant_id == restaurant_uuid,
                        Suppliers.name.ilike(supplier_name),
                        Suppliers.is_active == True,
                    )
                )
                if supplier:
                    supplier_uuid = supplier.id

            # No longer matching product aliases - we store raw names and search when user asks
            alias_matches = {}

            # Create document record
            document = Documents(
                restaurant_id=restaurant_uuid,
                supplier_id=supplier_uuid,
                doc_type="price_list",
                file_url=file_id,
                uploaded_at=dt.datetime.now(dt.UTC),
            )
            db.add(document)
            db.flush()

            # Determine status based on missing fields
            status = "pending_review"
            if not supplier_name:
                status = "awaiting_supplier"
            elif not currency:
                status = "awaiting_currency"

            items = extracted_data.get("items", [])
            page_numbers = {
                item.get("source_page")
                for item in items
                if isinstance(item.get("source_page"), int)
            }
            pages_processed = len(page_numbers)
            pages_total = max(page_numbers) if page_numbers else None
            finished_at = dt.datetime.now(dt.UTC)

            run.document_id = document.id
            run.status = "completed"
            run.current_stage = status
            run.pages_total = pages_total
            run.pages_processed = pages_processed
            run.finished_at = finished_at

            staging.document_id = document.id
            staging.extracted_data_json = extracted_data
            staging.product_alias_matches_json = alias_matches
            staging.status = status
            staging.pages_total = pages_total
            staging.pages_processed = pages_processed
            staging.finished_at = finished_at
            staging.error_message = None
            db.commit()

            # Send message based on status
            if status == "awaiting_supplier":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    field="supplier",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text="I couldn't find the supplier in this price list. Which supplier is it from?",
                    settings=settings,
                    session_uuid=session_uuid,
                )
            elif status == "awaiting_currency":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    field="currency",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text="What currency is this price list in? (e.g., USD, EUR)",
                    settings=settings,
                    session_uuid=session_uuid,
                )
            else:
                _clear_pending_file_processing_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _set_pending_file_processing_confirm_action(
                    db=db,
                    user_id=user_uuid,
                    restaurant_id=restaurant_uuid,
                    staging_id=staging.id,
                    supplier_id=supplier_uuid,
                )
                db.commit()
                # Format and send preview message with summary (not all items)
                items = extracted_data.get("items", [])
                total_items = len(items)
                
                lines = ["📋 **Price List Extracted**\n"]
                lines.append(f"**Supplier:** {supplier_name}")
                
                # Show contact info if available
                contact_name = extracted_data.get("contact_name")
                contact_email = extracted_data.get("contact_email")
                contact_phone = extracted_data.get("contact_phone")
                if contact_name:
                    lines.append(f"**Contact:** {contact_name}")
                if contact_email:
                    lines.append(f"**Email:** {contact_email}")
                if contact_phone:
                    lines.append(f"**Phone:** {contact_phone}")
                
                lines.append(f"**Currency:** {currency}")
                lines.append(f"**Total Items:** {total_items}")
                
                # Show effective date if available
                effective_date = extracted_data.get("effective_date")
                if effective_date:
                    lines.append(f"**Effective Date:** {effective_date}")
                
                # Show sample of first 5 items
                sample_size = min(5, total_items)
                if sample_size > 0:
                    lines.append(f"\n**Sample Items (first {sample_size} of {total_items}):**")
                    for i, item in enumerate(items[:sample_size], 1):
                        name = item.get("supplier_name_raw", item.get("name", "N/A"))
                        price = item.get("price", "N/A")
                        pack_size = item.get("pack_size_text", "")
                        
                        item_line = f"  {i}. {name}"
                        if pack_size:
                            item_line += f" ({pack_size})"
                        item_line += f" - {currency} {price}"
                        lines.append(item_line)
                    
                    if total_items > sample_size:
                        lines.append(f"  ... and {total_items - sample_size} more items")
                
                lines.append("\n---")
                lines.append("✅ Say **/confirm** to save all items to your database")
                lines.append("❓ Ask me to show specific items or categories")
                lines.append("✏️ Tell me if anything needs correcting")
                
                preview_text = "\n".join(lines)
                _send_message_with_telemetry(
                    db=db,
                    chat_id=chat_id,
                    text=preview_text,
                    settings=settings,
                    session_uuid=session_uuid,
                )

            # Record processing event
            db.add(
                ProcessingEvents(
                    session_id=session_uuid if session_uuid else staging.id,
                    at=dt.datetime.now(dt.UTC),
                    event="price_list_file_processed_v0",
                    payload_json=json.dumps(
                        {"staging_id": str(staging.id), "file_id": file_id}, ensure_ascii=False
                    ),
                    error=None,
                )
            )
            db.commit()

    except Exception as exc:
        logger.exception(
            "process_price_list_file_task_failed",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        # Send error message to user
        try:
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your price list file. Error: {str(exc)}",
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise


@celery_app.task(name="process_inventory_photo_task")
def process_inventory_photo_task(
    *,
    restaurant_id: str,
    file_id: str,
    chat_id: int,
    user_id: str,
    session_id: str | None = None,
) -> None:
    """Process an inventory photo: extract data, match products, store in staging."""
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=process_inventory_photo_task task_id=%s restaurant_id=%s file_id=%s",
        task_id,
        restaurant_id,
        file_id,
    )

    settings = get_settings()
    restaurant_uuid = _parse_uuid(restaurant_id)
    user_uuid = _parse_uuid(user_id)
    session_uuid = _parse_uuid(session_id) if session_id else None
    db_session: Session | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = (
                telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            )
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            run = FileProcessingRuns(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=None,
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                processing_type="inventory",
                status="processing",
                pages_total=1,
                pages_processed=0,
                current_stage="processing",
                error_message=None,
                started_at=now,
            )
            db.add(run)
            db.flush()

            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                run_id=run.id,
                document_id=None,
                processing_type="inventory",
                extracted_data_json={},
                product_alias_matches_json={},
                status="processing",
                pages_total=1,
                pages_processed=0,
                started_at=now,
            )
            db.add(staging)
            db.commit()

            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            # Extract inventory data (with DB-aware extraction)
            try:
                extracted_data = extract_inventory_data(file_bytes, mime_type, settings, db, filename)

                # Record telemetry for vision calls (uses fresh session internally)
                telemetry_results = extracted_data.pop("_telemetry_results", [])
                if telemetry_results:
                    _record_vision_telemetry(
                        session_uuid=session_uuid,
                        chat_id=chat_id,
                        telemetry_results=telemetry_results,
                        purpose_base="vision_inventory",
                        processing_type="inventory",
                    )
                    _record_file_processing_steps(
                        run_id=run.id,
                        telemetry_results=telemetry_results,
                        stage="vision_inventory",
                    )
            except Exception as exc:
                failed_at = dt.datetime.now(dt.UTC)
                run.status = "failed"
                run.current_stage = "failed"
                run.error_message = str(exc)
                run.finished_at = failed_at
                staging.status = "failed"
                staging.error_message = str(exc)
                staging.finished_at = failed_at
                db.commit()
                # Record error telemetry if possible (uses fresh session internally)
                if session_uuid:
                    try:
                        model, _, _ = _get_vision_settings(settings)
                        error_result = VisionCallResult(
                            content="",
                            model=model,
                            latency_ms=0,
                            usage=None,
                            openrouter_generation_id=None,
                            response_headers={},
                            response_data={},
                            error=f"Inventory extraction failed: {exc}",
                        )
                        _record_vision_telemetry(
                            session_uuid=session_uuid,
                            chat_id=chat_id,
                            telemetry_results=[error_result],
                            purpose_base="vision_inventory",
                            processing_type="inventory",
                        )
                        _record_file_processing_steps(
                            run_id=run.id,
                            telemetry_results=[error_result],
                            stage="vision_inventory_error",
                        )
                    except Exception:
                        logger.exception("failed_to_record_error_telemetry")
                raise

            # No longer matching product aliases - we store raw names and search when user asks
            alias_matches = {}

            finished_at = dt.datetime.now(dt.UTC)
            run.status = "completed"
            run.current_stage = "pending_review"
            run.pages_processed = 1
            run.finished_at = finished_at

            staging.extracted_data_json = extracted_data
            staging.product_alias_matches_json = alias_matches
            staging.status = "pending_review"
            staging.pages_processed = 1
            staging.finished_at = finished_at
            staging.error_message = None
            db.commit()
            _set_pending_file_processing_confirm_action(
                db=db,
                user_id=user_uuid,
                restaurant_id=restaurant_uuid,
                staging_id=staging.id,
            )
            db.commit()

            # Format and send preview message with summary
            items = extracted_data.get("items", [])
            total_items = len(items)
            
            lines = ["📸 **Inventory Extracted**\n"]
            lines.append(f"**Items Detected:** {total_items}")
            
            # Show sample of items (first 10 for inventory)
            sample_size = min(10, total_items)
            if sample_size > 0:
                lines.append(f"\n**Items ({sample_size} of {total_items}):**")
                for i, item in enumerate(items[:sample_size], 1):
                    product_name = item.get("product_name", "N/A")
                    quantity = item.get("quantity", "N/A")
                    unit = item.get("unit", "")
                    lines.append(f"  {i}. {product_name} - {quantity} {unit}")
                
                if total_items > sample_size:
                    lines.append(f"  ... and {total_items - sample_size} more items")
            
            lines.append("\n---")
            lines.append("✅ Say **/confirm** to save inventory counts")
            lines.append("❓ Ask to see all items if needed")
            lines.append("✏️ Tell me if anything needs correcting")
            
            preview_text = "\n".join(lines)
            _send_message_with_telemetry(
                db=db,
                chat_id=chat_id,
                text=preview_text,
                settings=settings,
                session_uuid=session_uuid,
            )

            # Record processing event
            db.add(
                ProcessingEvents(
                    session_id=session_uuid if session_uuid else staging.id,
                    at=dt.datetime.now(dt.UTC),
                    event="inventory_photo_processed_v0",
                    payload_json=json.dumps(
                        {"staging_id": str(staging.id), "file_id": file_id}, ensure_ascii=False
                    ),
                    error=None,
                )
            )
            db.commit()

    except Exception as exc:
        logger.exception(
            "process_inventory_photo_task_failed",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        # Send error message to user
        try:
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your inventory photo. Error: {str(exc)}",
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise
