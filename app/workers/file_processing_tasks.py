from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import time
import uuid
from typing import Any

from celery import current_task
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.documents import Documents
from app.db.models.file_processing_page_jobs import FileProcessingPageJobs
from app.db.models.file_processing_payloads import FileProcessingPayloads
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.file_processing_steps import FileProcessingSteps
from app.db.models.processing_events import ProcessingEvents
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.suppliers import Suppliers, normalize_supplier_name
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.user import User
from app.conversation.context import load_context, save_context
from app.ai.vision_client import VisionCallResult, _convert_pdf_pages_to_images
from app.processing.page_processor import (
    extract_json_with_retries,
    get_extraction_schema,
    merge_page_results,
    ocr_page_with_retries,
    save_page_snapshot,
)
from app.processing.webhooks import send_webhook_notification
from app.telegram.bot_api import (
    TelegramFileExpiredError,
    TelegramFileNetworkError,
    TelegramFileTooBigError,
    get_file_bytes,
    send_message,
)
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.telemetry import (
    record_llm_call,
    record_outgoing_message,
    schedule_openrouter_cost_backfill,
)
from app.workers.utils import _get_task_id, _parse_uuid

logger = logging.getLogger(__name__)

BROKER_PAYLOAD_MAX_BYTES = 200 * 1024
PAYLOAD_DB_PREFIX = "db:"
OCR_MAX_ATTEMPTS = 3
EXTRACTION_MAX_ATTEMPTS = 2
PAGE_JOB_STALE_SECONDS = 60 * 20  # Allow reclaiming "processing" jobs after 20 minutes.


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


def _encode_payload_bytes(
    *,
    db: Session,
    payload_bytes: bytes,
    run_id: uuid.UUID | None = None,
    page_index: int | None = None,
) -> str:
    """Return a broker-safe payload reference for file/page bytes."""
    encoded = base64.b64encode(payload_bytes)
    if len(encoded) <= BROKER_PAYLOAD_MAX_BYTES:
        return encoded.decode("ascii")

    payload = FileProcessingPayloads(
        run_id=run_id,
        page_index=page_index,
        payload_bytes=payload_bytes,
    )
    db.add(payload)
    db.flush()
    return f"{PAYLOAD_DB_PREFIX}{payload.id}"


def _resolve_payload_bytes(
    *, db: Session, payload_ref: str
) -> bytes:
    """Resolve a broker payload reference into bytes."""
    if payload_ref.startswith(PAYLOAD_DB_PREFIX):
        payload_id = payload_ref[len(PAYLOAD_DB_PREFIX) :]
        payload = db.get(FileProcessingPayloads, _parse_uuid(payload_id))
        if not payload:
            raise ValueError(f"Payload not found: {payload_id}")
        return bytes(payload.payload_bytes)

    return base64.b64decode(payload_ref)


def _ensure_restaurant_supplier_link(
    *,
    db: Session,
    restaurant_id: uuid.UUID | None,
    supplier_id: uuid.UUID,
) -> None:
    if restaurant_id is None:
        return
    existing = db.scalar(
        select(RestaurantSuppliers).where(
            RestaurantSuppliers.restaurant_id == restaurant_id,
            RestaurantSuppliers.supplier_id == supplier_id,
        )
    )
    if existing:
        return
    link = RestaurantSuppliers(
        restaurant_id=restaurant_id,
        supplier_id=supplier_id,
        status="active",
    )
    db.add(link)
    db.flush()


def _get_or_create_supplier(
    *,
    db: Session,
    name: str,
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID | None,
    defaults: dict[str, Any] | None = None,
) -> Suppliers:
    normalized = normalize_supplier_name(name)
    supplier = db.scalar(
        select(Suppliers).where(
            Suppliers.user_id == user_id,
            Suppliers.name_normalized == normalized,
            Suppliers.is_active == True,
        )
    )
    if not supplier:
        values = defaults or {}
        supplier = Suppliers(
            user_id=user_id,
            name=name,
            name_normalized=normalized,
            is_active=True,
            **values,
        )
        db.add(supplier)
        db.flush()

    _ensure_restaurant_supplier_link(db=db, restaurant_id=restaurant_id, supplier_id=supplier.id)
    return supplier


def _enqueue_coordinator_task(
    *,
    db: Session,
    run: FileProcessingRuns,
    file_bytes: bytes,
    mime_type: str,
    filename: str | None,
) -> None:
    payload_ref = _encode_payload_bytes(
        db=db,
        payload_bytes=file_bytes,
        run_id=run.id,
    )
    db.commit()
    process_file_api_task.apply_async(
        args=[str(run.id), payload_ref, mime_type, filename or "unknown"],
    )


def _mark_run_failed(
    *,
    db: Session,
    run_id: uuid.UUID,
    error_message: str,
) -> None:
    failed_at = dt.datetime.now(dt.UTC)
    run = db.get(FileProcessingRuns, run_id)
    if run:
        run.status = "failed"
        run.current_stage = "failed"
        run.error_message = error_message
        run.finished_at = failed_at

    staging = db.scalar(
        select(FileProcessingStaging).where(FileProcessingStaging.run_id == run_id)
    )
    if staging:
        staging.status = "failed"
        staging.error_message = error_message
        staging.finished_at = failed_at
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
    """Process an invoice file: enqueue coordinator and notify user on completion."""
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
    run_id: uuid.UUID | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

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
                current_stage="intake",
                error_message=None,
                started_at=now,
                source="telegram",
                chat_id=chat_id,
                session_id=session_uuid,
                supplier_id=_parse_uuid(supplier_id) if supplier_id else None,
            )
            db.add(run)
            db.flush()
            run_id = run.id

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
                supplier_id=_parse_uuid(supplier_id) if supplier_id else None,
            )
            db.add(staging)
            db.commit()

            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            _enqueue_coordinator_task(
                db=db,
                run=run,
                file_bytes=file_bytes,
                mime_type=mime_type,
                filename=filename,
            )

    except TelegramFileExpiredError as exc:
        logger.error(
            "process_invoice_file_task_file_expired",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message="Telegram file expired",
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⏰ Your invoice file is no longer available on Telegram.\n\n"
                    "Telegram files expire after 24-48 hours. To process this invoice, "
                    "please re-upload the PDF.\n\n"
                    "💡 Tip: If you were resuming processing, I'll pick up where we left off "
                    "after you re-upload."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileTooBigError as exc:
        logger.error(
            "process_invoice_file_task_file_too_big",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "📦 Your invoice file is too large.\n\n"
                    f"{str(exc)}\n\n"
                    "Please try compressing the PDF or taking a photo instead."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileNetworkError as exc:
        logger.warning(
            "process_invoice_file_task_network_error",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⚠️ Temporary error downloading your invoice file.\n\n"
                    "This is usually a temporary issue with Telegram's servers. "
                    "Please try re-uploading the PDF in a few minutes."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

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
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your invoice. Error: {str(exc)}",
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
    """Process a price list file: enqueue coordinator and notify user on completion."""
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
    run_id: uuid.UUID | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

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
                current_stage="intake",
                error_message=None,
                started_at=now,
                source="telegram",
                chat_id=chat_id,
                session_id=session_uuid,
                supplier_id=_parse_uuid(supplier_id) if supplier_id else None,
            )
            db.add(run)
            db.flush()
            run_id = run.id

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
                supplier_id=_parse_uuid(supplier_id) if supplier_id else None,
            )
            db.add(staging)
            db.commit()

            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            _enqueue_coordinator_task(
                db=db,
                run=run,
                file_bytes=file_bytes,
                mime_type=mime_type,
                filename=filename,
            )

    except TelegramFileExpiredError as exc:
        logger.error(
            "process_price_list_file_task_file_expired",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message="Telegram file expired",
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⏰ Your price list file is no longer available on Telegram.\n\n"
                    "Telegram files expire after 24-48 hours. To process this price list, "
                    "please re-upload the PDF.\n\n"
                    "💡 Tip: If you were resuming processing, I'll pick up where we left off "
                    "after you re-upload."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileTooBigError as exc:
        logger.error(
            "process_price_list_file_task_file_too_big",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "📦 Your price list file is too large.\n\n"
                    f"{str(exc)}\n\n"
                    "Please try compressing the PDF or taking a photo instead."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileNetworkError as exc:
        logger.warning(
            "process_price_list_file_task_network_error",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⚠️ Temporary error downloading your price list file.\n\n"
                    "This is usually a temporary issue with Telegram's servers. "
                    "Please try re-uploading the PDF in a few minutes."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

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
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your price list. Error: {str(exc)}",
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
    """Process an inventory photo: enqueue coordinator and notify user on completion."""
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
    run_id: uuid.UUID | None = None

    try:
        with worker_db_session() as db:
            db_session = db
            now = dt.datetime.now(dt.UTC)

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
                current_stage="intake",
                error_message=None,
                started_at=now,
                source="telegram",
                chat_id=chat_id,
                session_id=session_uuid,
            )
            db.add(run)
            db.flush()
            run_id = run.id

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

            file_bytes = get_file_bytes(file_id=file_id, settings=settings)

            _enqueue_coordinator_task(
                db=db,
                run=run,
                file_bytes=file_bytes,
                mime_type=mime_type,
                filename=filename,
            )

    except TelegramFileExpiredError as exc:
        logger.error(
            "process_inventory_photo_task_file_expired",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message="Telegram file expired",
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⏰ Your inventory photo is no longer available on Telegram.\n\n"
                    "Telegram files expire after 24-48 hours. To process this inventory, "
                    "please re-upload the photo."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileTooBigError as exc:
        logger.error(
            "process_inventory_photo_task_file_too_big",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "📦 Your inventory photo is too large.\n\n"
                    f"{str(exc)}\n\n"
                    "Please try compressing the image or taking a new photo."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

    except TelegramFileNetworkError as exc:
        logger.warning(
            "process_inventory_photo_task_network_error",
            extra={
                "error": repr(exc),
                "restaurant_id": restaurant_id,
                "file_id": file_id,
                "chat_id": chat_id,
            },
        )
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
            _send_message_with_telemetry(
                db=db_session,
                chat_id=chat_id,
                text=(
                    "⚠️ Temporary error downloading your inventory photo.\n\n"
                    "This is usually a temporary issue with Telegram's servers. "
                    "Please try re-uploading the photo in a few minutes."
                ),
                settings=settings,
                session_uuid=session_uuid,
            )
        except Exception:
            pass
        raise

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
        try:
            if db_session and run_id:
                _mark_run_failed(
                    db=db_session,
                    run_id=run_id,
                    error_message=str(exc),
                )
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


@celery_app.task(name="process_file_api_task")
def process_file_api_task(
    run_id: str,
    file_bytes_base64: str,
    mime_type: str,
    filename: str,
) -> dict[str, Any]:
    """
    Coordinator task that creates page jobs and enqueues per-page processing.

    Accepts base64-encoded payloads or payload references with the "db:" prefix.
    """
    logger.info(
        "process_file_api_task_started",
        extra={
            "run_id": run_id,
            "mime_type": mime_type,
            # Avoid clobbering logging.LogRecord's reserved "filename" attribute.
            "uploaded_filename": filename,
        },
    )

    run_uuid = _parse_uuid(run_id)

    with worker_db_session() as db:
        run = db.get(FileProcessingRuns, run_uuid)
        if not run:
            raise ValueError(f"FileProcessingRun not found: {run_id}")

        try:
            file_bytes = _resolve_payload_bytes(db=db, payload_ref=file_bytes_base64)
        except Exception as exc:
            _mark_run_failed(db=db, run_id=run_uuid, error_message=str(exc))
            raise

        now = dt.datetime.now(dt.UTC)
        if not run.started_at:
            run.started_at = now
        run.status = "processing"
        run.current_stage = "pdf_to_images" if mime_type == "application/pdf" else "image_to_page"
        db.commit()

        try:
            if mime_type == "application/pdf":
                pages = _convert_pdf_pages_to_images(file_bytes)
                page_mime_type = "image/png"
            else:
                pages = [(1, file_bytes)]
                page_mime_type = mime_type
        except Exception as exc:
            _mark_run_failed(db=db, run_id=run_uuid, error_message=str(exc))
            raise

        total_pages = len(pages)
        run.pages_total = total_pages
        run.pages_processed = 0
        run.current_stage = "page_jobs_enqueued"

        staging = db.scalar(
            select(FileProcessingStaging).where(FileProcessingStaging.run_id == run.id)
        )
        if staging:
            staging.pages_total = total_pages
            staging.pages_processed = 0
            if not staging.started_at:
                staging.started_at = run.started_at or now
        db.commit()

        for page_index, page_bytes in pages:
            existing = db.scalar(
                select(FileProcessingPageJobs).where(
                    FileProcessingPageJobs.run_id == run.id,
                    FileProcessingPageJobs.page_index == page_index,
                )
            )
            if existing:
                job = existing
            else:
                job = FileProcessingPageJobs(
                    run_id=run.id,
                    page_index=page_index,
                    status="pending",
                )
                db.add(job)
                db.flush()

            if job.status in {"completed", "failed_ocr", "failed_extraction"}:
                logger.info(
                    "page_job_enqueue_skipped_terminal run_id=%s page_index=%s status=%s",
                    str(run.id),
                    page_index,
                    job.status,
                )
                continue

            payload_ref = _encode_payload_bytes(
                db=db,
                payload_bytes=page_bytes,
                run_id=run.id,
                page_index=page_index,
            )
            db.commit()
            task_id = f"{run.id}:{page_index}"
            payload_kind = "db" if payload_ref.startswith(PAYLOAD_DB_PREFIX) else "inline"
            logger.info(
                "page_job_enqueued run_id=%s page_index=%s task_id=%s payload_kind=%s page_mime_type=%s",
                str(run.id),
                page_index,
                task_id,
                payload_kind,
                page_mime_type,
            )
            try:
                process_page_job_task.apply_async(
                    args=[str(run.id), page_index, payload_ref, page_mime_type],
                    task_id=task_id,
                )
            except Exception:
                logger.exception(
                    "page_job_enqueue_failed run_id=%s page_index=%s task_id=%s",
                    str(run.id),
                    page_index,
                    task_id,
                )

        return {
            "run_id": run_id,
            "status": "enqueued",
            "pages_total": total_pages,
        }


def _get_page_job_counts(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    total = db.scalar(
        select(func.count()).where(FileProcessingPageJobs.run_id == run_id)
    ) or 0
    completed = db.scalar(
        select(func.count()).where(
            FileProcessingPageJobs.run_id == run_id,
            FileProcessingPageJobs.status == "completed",
        )
    ) or 0
    failed = db.scalar(
        select(func.count()).where(
            FileProcessingPageJobs.run_id == run_id,
            FileProcessingPageJobs.status.in_(["failed_ocr", "failed_extraction"]),
        )
    ) or 0
    processing = db.scalar(
        select(func.count()).where(
            FileProcessingPageJobs.run_id == run_id,
            FileProcessingPageJobs.status == "processing",
        )
    ) or 0

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "processing": processing,
    }


def _update_run_progress(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    counts = _get_page_job_counts(db, run_id)
    run = db.get(FileProcessingRuns, run_id)
    if run:
        if counts["total"] > 0:
            run.pages_total = counts["total"]
        run.pages_processed = counts["completed"]

    staging = db.scalar(
        select(FileProcessingStaging).where(FileProcessingStaging.run_id == run_id)
    )
    if staging:
        if counts["total"] > 0:
            staging.pages_total = counts["total"]
        staging.pages_processed = counts["completed"]

    db.commit()
    return counts


@celery_app.task(name="process_page_job_task")
def process_page_job_task(
    run_id: str,
    page_index: int,
    page_payload: str,
    mime_type: str,
) -> None:
    settings = get_settings()
    run_uuid = _parse_uuid(run_id)
    total_pages = 0
    markdown_text = ""
    page_bytes: bytes | None = None
    ocr_attempts = 0
    extraction_attempts = 0
    processing_type = ""
    task_uuid = getattr(getattr(current_task, "request", None), "id", None)

    # Session A: do all DB reads and claim the job, then close the session before
    # calling external OCR/extraction (prevents idle-in-transaction timeouts).
    with worker_db_session() as db:
        run = db.get(FileProcessingRuns, run_uuid)
        if not run:
            raise ValueError(f"FileProcessingRun not found: {run_id}")

        processing_type = run.processing_type
        total_pages = run.pages_total or 0

        job = db.scalar(
            select(FileProcessingPageJobs).where(
                FileProcessingPageJobs.run_id == run_uuid,
                FileProcessingPageJobs.page_index == page_index,
            )
        )
        if not job:
            job = FileProcessingPageJobs(
                run_id=run_uuid,
                page_index=page_index,
                status="pending",
            )
            db.add(job)
            db.flush()

        if job.status in {"completed", "failed_ocr", "failed_extraction"}:
            logger.info(
                "page_job_skipped_terminal run_id=%s page_index=%s task_id=%s status=%s",
                run_id,
                page_index,
                task_uuid,
                job.status,
            )
            db.commit()
            return

        if job.status == "processing":
            stale_before = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=PAGE_JOB_STALE_SECONDS)
            reclaimed = db.execute(
                update(FileProcessingPageJobs)
                .where(
                    FileProcessingPageJobs.id == job.id,
                    FileProcessingPageJobs.status == "processing",
                    FileProcessingPageJobs.updated_at < stale_before,
                )
                .values(status="pending", error_message=None)
            )
            if reclaimed.rowcount == 1:
                logger.warning(
                    "page_job_reclaimed_stale run_id=%s page_index=%s task_id=%s stale_seconds=%s",
                    run_id,
                    page_index,
                    task_uuid,
                    PAGE_JOB_STALE_SECONDS,
                )
                db.commit()
                job.status = "pending"

        if job.status != "pending":
            # Another worker is processing it.
            logger.info(
                "page_job_skipped_not_pending run_id=%s page_index=%s task_id=%s status=%s",
                run_id,
                page_index,
                task_uuid,
                job.status,
            )
            db.commit()
            return

        updated = db.execute(
            update(FileProcessingPageJobs)
            .where(
                FileProcessingPageJobs.id == job.id,
                FileProcessingPageJobs.status == "pending",
            )
            .values(status="processing", error_message=None)
        )
        if updated.rowcount != 1:
            db.rollback()
            return

        run.current_stage = f"page_{page_index}_ocr"
        logger.info(
            "page_job_claimed run_id=%s page_index=%s task_id=%s processing_type=%s mime_type=%s pages_total=%s",
            run_id,
            page_index,
            task_uuid,
            processing_type,
            mime_type,
            total_pages,
        )
        db.commit()

        ocr_step = db.scalar(
            select(FileProcessingSteps).where(
                FileProcessingSteps.run_id == run_uuid,
                FileProcessingSteps.stage == "ocr",
                FileProcessingSteps.page_index == page_index,
                FileProcessingSteps.status == "completed",
            )
        )
        if ocr_step and ocr_step.output_text:
            markdown_text = ocr_step.output_text
        else:
            # If OCR is missing, we need the page bytes. Fetch them now and then close the DB session.
            save_page_snapshot(
                run_id=run_uuid,
                page_index=page_index,
                stage="ocr",
                output_text="",
                status="started",
                db=db,
            )
            page_bytes = _resolve_payload_bytes(db=db, payload_ref=page_payload)

    # OCR (no DB session held open)
    if not markdown_text:
        try:
            if page_bytes is None:
                # Shouldn't happen, but keep a defensive fallback.
                with worker_db_session() as db:
                    page_bytes = _resolve_payload_bytes(db=db, payload_ref=page_payload)
            logger.info(
                "page_job_ocr_start run_id=%s page_index=%s task_id=%s",
                run_id,
                page_index,
                task_uuid,
            )
            ocr_start = time.time()
            ocr_result, ocr_attempts = ocr_page_with_retries(
                page_image_bytes=page_bytes,
                page_num=page_index,
                total_pages=total_pages or page_index,
                settings=settings,
                mime_type=mime_type,
                max_attempts=OCR_MAX_ATTEMPTS,
            )
            ocr_latency_ms = int((time.time() - ocr_start) * 1000)
            markdown_text = ocr_result.content or ""
            logger.info(
                "page_job_ocr_done run_id=%s page_index=%s task_id=%s attempts=%s latency_ms=%s output_chars=%s",
                run_id,
                page_index,
                task_uuid,
                ocr_attempts,
                ocr_latency_ms,
                len(markdown_text),
            )
        except Exception as exc:
            with worker_db_session() as db:
                job = db.scalar(
                    select(FileProcessingPageJobs).where(
                        FileProcessingPageJobs.run_id == run_uuid,
                        FileProcessingPageJobs.page_index == page_index,
                    )
                )
                if job:
                    job.status = "failed_ocr"
                    job.error_message = str(exc)
                    job.ocr_retries = max(OCR_MAX_ATTEMPTS - 1, 0)
                save_page_snapshot(
                    run_id=run_uuid,
                    page_index=page_index,
                    stage="ocr",
                    output_text="",
                    status="failed",
                    error_message=str(exc),
                    db=db,
                )
                db.commit()
                _update_run_progress(db, run_uuid)
                maybe_finalize_run(run_uuid, db)
            return

        if not markdown_text:
            with worker_db_session() as db:
                job = db.scalar(
                    select(FileProcessingPageJobs).where(
                        FileProcessingPageJobs.run_id == run_uuid,
                        FileProcessingPageJobs.page_index == page_index,
                    )
                )
                if job:
                    job.status = "failed_ocr"
                    job.error_message = "OCR returned empty content"
                    job.ocr_retries = max(OCR_MAX_ATTEMPTS - 1, 0)
                save_page_snapshot(
                    run_id=run_uuid,
                    page_index=page_index,
                    stage="ocr",
                    output_text="",
                    status="failed",
                    error_message="OCR returned empty content",
                    db=db,
                )
                db.commit()
                _update_run_progress(db, run_uuid)
                maybe_finalize_run(run_uuid, db)
            return

        # Session B: persist OCR snapshot in its own short transaction
        with worker_db_session() as db:
            save_page_snapshot(
                run_id=run_uuid,
                page_index=page_index,
                stage="ocr",
                output_text=markdown_text,
                status="completed",
                db=db,
            )

    # Extraction (no DB session held open)
    with worker_db_session() as db:
        run = db.get(FileProcessingRuns, run_uuid)
        if run:
            run.current_stage = f"page_{page_index}_extraction"
        save_page_snapshot(
            run_id=run_uuid,
            page_index=page_index,
            stage="extraction",
            output_text="",
            status="started",
            db=db,
        )
        db.commit()

    try:
        db_schema = get_extraction_schema(processing_type)
        logger.info(
            "page_job_extraction_start run_id=%s page_index=%s task_id=%s processing_type=%s",
            run_id,
            page_index,
            task_uuid,
            processing_type,
        )
        extraction_start = time.time()
        extraction_result, _, extraction_attempts = extract_json_with_retries(
            markdown_text=markdown_text,
            page_num=page_index,
            processing_type=processing_type,
            db_schema=db_schema,
            settings=settings,
            max_attempts=EXTRACTION_MAX_ATTEMPTS,
        )
        extraction_latency_ms = int((time.time() - extraction_start) * 1000)
        extraction_text = extraction_result.content or "{}"
        logger.info(
            "page_job_extraction_done run_id=%s page_index=%s task_id=%s attempts=%s latency_ms=%s output_chars=%s",
            run_id,
            page_index,
            task_uuid,
            extraction_attempts,
            extraction_latency_ms,
            len(extraction_text),
        )
    except Exception as exc:
        with worker_db_session() as db:
            job = db.scalar(
                select(FileProcessingPageJobs).where(
                    FileProcessingPageJobs.run_id == run_uuid,
                    FileProcessingPageJobs.page_index == page_index,
                )
            )
            if job:
                job.status = "failed_extraction"
                job.error_message = str(exc)
                job.extraction_retries = max(EXTRACTION_MAX_ATTEMPTS - 1, 0)
            save_page_snapshot(
                run_id=run_uuid,
                page_index=page_index,
                stage="extraction",
                output_text="",
                status="failed",
                error_message=str(exc),
                db=db,
            )
            db.commit()
            _update_run_progress(db, run_uuid)
            maybe_finalize_run(run_uuid, db)
        return

    # Session C: persist extraction snapshot + mark job completed in its own short transaction
    with worker_db_session() as db:
        run = db.get(FileProcessingRuns, run_uuid)
        if not run:
            raise ValueError(f"FileProcessingRun not found: {run_id}")

        job = db.scalar(
            select(FileProcessingPageJobs).where(
                FileProcessingPageJobs.run_id == run_uuid,
                FileProcessingPageJobs.page_index == page_index,
            )
        )
        if not job:
            # Shouldn't happen, but avoid crashing the worker.
            job = FileProcessingPageJobs(
                run_id=run_uuid,
                page_index=page_index,
                status="processing",
            )
            db.add(job)
            db.flush()

        save_page_snapshot(
            run_id=run_uuid,
            page_index=page_index,
            stage="extraction",
            output_text=extraction_text,
            status="completed",
            db=db,
        )

        job.status = "completed"
        if ocr_attempts:
            job.ocr_retries = max(ocr_attempts - 1, 0)
        if extraction_attempts:
            job.extraction_retries = max(extraction_attempts - 1, 0)
        job.error_message = None
        db.commit()

        counts = _update_run_progress(db, run_uuid)

        if run.webhook_url and counts["total"] > 0:
            completed = counts["completed"]
            total = counts["total"]
            if completed % 10 == 0 or (counts["completed"] + counts["failed"]) >= total:
                progress_pct = (completed / total) * 100 if total else 0
                send_webhook_notification(
                    webhook_url=run.webhook_url,
                    run_id=run.id,
                    status="processing",
                    progress_percentage=progress_pct,
                    pages_processed=completed,
                    pages_total=total,
                )

        logger.info(
            "page_job_completed run_id=%s page_index=%s task_id=%s pages_completed=%s pages_total=%s pages_failed=%s",
            run_id,
            page_index,
            task_uuid,
            counts["completed"],
            counts["total"],
            counts["failed"],
        )
        maybe_finalize_run(run_uuid, db)


def maybe_finalize_run(run_id: uuid.UUID, db: Session) -> None:
    counts = _get_page_job_counts(db, run_id)
    total = counts["total"]
    if total <= 0:
        return
    terminal = counts["completed"] + counts["failed"]
    if terminal < total:
        return

    updated = db.execute(
        update(FileProcessingRuns)
        .where(
            FileProcessingRuns.id == run_id,
            or_(
                FileProcessingRuns.current_stage.is_(None),
                FileProcessingRuns.current_stage.notin_(["merge_pending", "finalize"]),
            ),
        )
        .values(current_stage="merge_pending")
    )
    if updated.rowcount != 1:
        db.rollback()
        return
    db.commit()
    finalize_run_task.apply_async(args=[str(run_id)])


@celery_app.task(name="finalize_run_task")
def finalize_run_task(run_id: str) -> None:
    settings = get_settings()
    run_uuid = _parse_uuid(run_id)

    with worker_db_session() as db:
        run = db.get(FileProcessingRuns, run_uuid)
        if not run:
            raise ValueError(f"FileProcessingRun not found: {run_id}")

        staging = db.scalar(
            select(FileProcessingStaging).where(FileProcessingStaging.run_id == run_uuid)
        )
        if not staging:
            logger.error("finalize_run_task_no_staging", extra={"run_id": run_id})
            return

        failed_pages = db.scalars(
            select(FileProcessingPageJobs.page_index)
            .where(
                FileProcessingPageJobs.run_id == run_uuid,
                FileProcessingPageJobs.status.in_(["failed_ocr", "failed_extraction"]),
            )
            .order_by(FileProcessingPageJobs.page_index)
        ).all()
        failed_pages_list = [int(page) for page in failed_pages]

        try:
            merged_data = merge_page_results(run_uuid, db)
        except Exception as exc:
            _mark_run_failed(db=db, run_id=run_uuid, error_message=str(exc))
            raise
        if not isinstance(merged_data, dict):
            merged_data = {}

        counts = _update_run_progress(db, run_uuid)

        supplier_uuid = run.supplier_id or staging.supplier_id
        supplier_name = (
            merged_data.get("supplier_name", "").strip()
            if isinstance(merged_data, dict) and merged_data.get("supplier_name")
            else ""
        )
        currency = (
            merged_data.get("currency", "").strip()
            if isinstance(merged_data, dict) and merged_data.get("currency")
            else ""
        )

        if run.processing_type in {"invoice", "price_list"}:
            if not supplier_uuid and supplier_name:
                defaults = {}
                if run.processing_type == "price_list":
                    defaults = {
                        "contact_name": merged_data.get("contact_name"),
                        "contact_email": merged_data.get("contact_email"),
                        "contact_phone": merged_data.get("contact_phone"),
                        "currency": merged_data.get("currency"),
                    }
                supplier = _get_or_create_supplier(
                    db=db,
                    name=supplier_name,
                    user_id=run.user_id,
                    restaurant_id=run.restaurant_id,
                    defaults=defaults,
                )
                supplier_uuid = supplier.id

            if supplier_uuid:
                run.supplier_id = supplier_uuid
                staging.supplier_id = supplier_uuid
                _ensure_restaurant_supplier_link(
                    db=db,
                    restaurant_id=run.restaurant_id,
                    supplier_id=supplier_uuid,
                )
                if not supplier_name:
                    supplier_row = db.get(Suppliers, supplier_uuid)
                    if supplier_row:
                        supplier_name = supplier_row.name

        if run.source == "telegram" and run.processing_type in {"invoice", "price_list"}:
            if not run.document_id:
                document = Documents(
                    restaurant_id=run.restaurant_id,
                    supplier_id=supplier_uuid,
                    doc_type=run.processing_type,
                    file_url=run.file_id,
                    uploaded_at=dt.datetime.now(dt.UTC),
                )
                db.add(document)
                db.flush()
                run.document_id = document.id
                staging.document_id = document.id

        status = "pending_review"
        if run.processing_type in {"invoice", "price_list"}:
            if not supplier_uuid and not supplier_name:
                status = "awaiting_supplier"
            elif not currency:
                status = "awaiting_currency"

        staging.extracted_data_json = merged_data if isinstance(merged_data, dict) else {}
        staging.product_alias_matches_json = {}
        staging.status = status
        staging.error_message = None
        staging.finished_at = dt.datetime.now(dt.UTC)

        run.status = "completed"
        run.current_stage = "finalize"
        run.finished_at = dt.datetime.now(dt.UTC)
        if failed_pages_list:
            run.error_message = f"{len(failed_pages_list)} pages failed: {failed_pages_list}"
        else:
            run.error_message = None

        db.commit()

        if run.source == "telegram" and run.chat_id:
            if status == "awaiting_supplier":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=run.user_id,
                    restaurant_id=run.restaurant_id,
                    staging_id=staging.id,
                    field="supplier",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=run.chat_id,
                    text=(
                        "I couldn't find the supplier in this document. "
                        "What supplier is it from?"
                    ),
                    settings=settings,
                    session_uuid=run.session_id,
                )
            elif status == "awaiting_currency":
                _set_pending_file_processing_action(
                    db=db,
                    user_id=run.user_id,
                    restaurant_id=run.restaurant_id,
                    staging_id=staging.id,
                    field="currency",
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _send_message_with_telemetry(
                    db=db,
                    chat_id=run.chat_id,
                    text=(
                        "What currency is this document in? (e.g., USD, EUR)"
                    ),
                    settings=settings,
                    session_uuid=run.session_id,
                )
            else:
                _clear_pending_file_processing_action(
                    db=db,
                    user_id=run.user_id,
                    restaurant_id=run.restaurant_id,
                    supplier_id=supplier_uuid,
                )
                db.commit()
                _set_pending_file_processing_confirm_action(
                    db=db,
                    user_id=run.user_id,
                    restaurant_id=run.restaurant_id,
                    staging_id=staging.id,
                    supplier_id=supplier_uuid,
                )
                db.commit()

                if run.processing_type == "invoice":
                    line_items = merged_data.get("line_items", []) if isinstance(merged_data, dict) else []
                    total_items = len(line_items)
                    lines = ["📄 **Invoice Extracted**\n"]
                    lines.append(f"**Supplier:** {supplier_name or 'N/A'}")
                    lines.append(f"**Invoice #:** {merged_data.get('invoice_number', 'N/A')}")
                    lines.append(f"**Date:** {merged_data.get('invoice_date', 'N/A')}")
                    due_date = merged_data.get("due_date") if isinstance(merged_data, dict) else None
                    if due_date:
                        lines.append(f"**Due Date:** {due_date}")
                    lines.append(f"**Currency:** {currency or 'N/A'}")
                    lines.append(f"**Total:** {currency or ''} {merged_data.get('total', 'N/A')}")
                    lines.append(f"**Line Items:** {total_items}")

                    sample_size = min(10, total_items)
                    if sample_size > 0:
                        lines.append(f"\n**Line Items ({sample_size} of {total_items}):**")
                        for i, item in enumerate(line_items[:sample_size], 1):
                            desc = item.get("description_raw", item.get("description", "N/A"))
                            qty = item.get("quantity", "N/A")
                            unit = item.get("unit", "")
                            total = item.get("line_total", "N/A")
                            lines.append(f"  {i}. {desc} - {qty} {unit} = {currency or ''} {total}")
                        if total_items > sample_size:
                            lines.append(f"  ... and {total_items - sample_size} more items")

                    lines.append("\n---")
                    lines.append("✅ Say **/confirm** to save this invoice")
                    lines.append("❓ Ask to see all line items if needed")
                    lines.append("✏️ Tell me if anything needs correcting")

                    _send_message_with_telemetry(
                        db=db,
                        chat_id=run.chat_id,
                        text="\n".join(lines),
                        settings=settings,
                        session_uuid=run.session_id,
                    )

                elif run.processing_type == "price_list":
                    items = merged_data.get("items", []) if isinstance(merged_data, dict) else []
                    total_items = len(items)
                    lines = ["📋 **Price List Extracted**\n"]
                    lines.append(f"**Supplier:** {supplier_name or 'N/A'}")
                    lines.append(f"**Currency:** {currency or 'N/A'}")
                    lines.append(f"**Items:** {total_items}")

                    sample_size = min(10, total_items)
                    if sample_size > 0:
                        lines.append(f"\n**Items ({sample_size} of {total_items}):**")
                        for i, item in enumerate(items[:sample_size], 1):
                            name = item.get("supplier_name_raw", item.get("name", "N/A"))
                            price = item.get("price", "N/A")
                            item_currency = item.get("currency", currency or "N/A")
                            unit_basis = item.get("unit_basis", "")
                            pack_size = item.get("pack_size_text", "")
                            min_order_qty = item.get("min_order_qty")

                            item_line = f"  {i}. {name}"
                            if pack_size:
                                item_line += f" (pack_size_text: {pack_size})"
                            if unit_basis:
                                item_line += f" (unit_basis: {unit_basis})"
                            if min_order_qty is not None:
                                item_line += f" (min_order_qty: {min_order_qty})"
                            item_line += f" - {price} {item_currency}"
                            lines.append(item_line)
                        if total_items > sample_size:
                            lines.append(f"  ... and {total_items - sample_size} more items")

                    lines.append("\n---")
                    lines.append("✅ Say **/confirm** to save this price list")
                    lines.append("❓ Ask to see all items if needed")
                    lines.append("✏️ Tell me if anything needs correcting")

                    _send_message_with_telemetry(
                        db=db,
                        chat_id=run.chat_id,
                        text="\n".join(lines),
                        settings=settings,
                        session_uuid=run.session_id,
                    )

                elif run.processing_type == "inventory":
                    items = merged_data.get("items", []) if isinstance(merged_data, dict) else []
                    total_items = len(items)
                    lines = ["📸 **Inventory Extracted**\n"]
                    lines.append(f"**Items Detected:** {total_items}")

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

                    _send_message_with_telemetry(
                        db=db,
                        chat_id=run.chat_id,
                        text="\n".join(lines),
                        settings=settings,
                        session_uuid=run.session_id,
                    )

        if run.source == "telegram":
            event_name = None
            if run.processing_type == "invoice":
                event_name = "invoice_file_processed_v0"
            elif run.processing_type == "price_list":
                event_name = "price_list_file_processed_v0"
            elif run.processing_type == "inventory":
                event_name = "inventory_photo_processed_v0"

            if event_name:
                db.add(
                    ProcessingEvents(
                        session_id=run.session_id if run.session_id else staging.id,
                        at=dt.datetime.now(dt.UTC),
                        event=event_name,
                        payload_json=json.dumps(
                            {"staging_id": str(staging.id), "file_id": run.file_id},
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )
                db.commit()

        if run.webhook_url:
            progress_pct = 100.0 if counts["total"] else 0.0
            send_webhook_notification(
                webhook_url=run.webhook_url,
                run_id=run.id,
                status="completed",
                progress_percentage=progress_pct,
                pages_processed=counts["completed"],
                pages_total=counts["total"],
                staging_id=staging.id,
                error_message=run.error_message,
            )


@celery_app.task(name="cleanup_file_processing_task")
def cleanup_file_processing_task() -> None:
    with worker_db_session() as db:
        now = dt.datetime.now(dt.UTC)
        expired_run_ids = db.scalars(
            select(FileProcessingRuns.id)
            .join(
                FileProcessingStaging,
                FileProcessingStaging.run_id == FileProcessingRuns.id,
            )
            .where(
                FileProcessingRuns.finished_at.isnot(None),
                FileProcessingStaging.expires_at <= now,
            )
        ).all()

        if not expired_run_ids:
            return

        db.query(FileProcessingPageJobs).filter(
            FileProcessingPageJobs.run_id.in_(expired_run_ids)
        ).delete(synchronize_session=False)
        db.query(FileProcessingSteps).filter(
            FileProcessingSteps.run_id.in_(expired_run_ids)
        ).delete(synchronize_session=False)
        db.query(FileProcessingPayloads).filter(
            FileProcessingPayloads.run_id.in_(expired_run_ids)
        ).delete(synchronize_session=False)
        db.commit()
