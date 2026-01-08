from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.documents import Documents
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.processing_events import ProcessingEvents
from app.db.models.suppliers import Suppliers
from app.db.models.telegram_messages import TelegramMessages
from app.processing.file_processor import (
    extract_inventory_data,
    extract_invoice_data,
    extract_price_list_data,
    match_product_aliases,
)
from app.telegram.bot_api import get_file_bytes, send_message
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id, _parse_uuid

logger = logging.getLogger(__name__)


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

    try:
        with worker_db_session() as db:
            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)
            
            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            # Extract invoice data
            extracted_data = extract_invoice_data(file_bytes, mime_type, settings, filename)

            # Find or create supplier if not provided
            supplier_uuid = None
            if supplier_id:
                supplier_uuid = _parse_uuid(supplier_id)
            else:
                # Try to find supplier by name from extracted data
                supplier_name = extracted_data.get("supplier_name", "").strip()
                if supplier_name:
                    supplier = db.scalar(
                        select(Suppliers).where(
                            Suppliers.restaurant_id == restaurant_uuid,
                            Suppliers.name.ilike(supplier_name),
                            Suppliers.is_active == True,
                        )
                    )
                    if supplier:
                        supplier_uuid = supplier.id

            # Match product aliases for line items
            supplier_names = [
                item.get("description", "") for item in extracted_data.get("line_items", [])
            ]
            alias_matches = match_product_aliases(restaurant_uuid, supplier_names, db, settings)

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

            # Create staging record
            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=document.id,
                processing_type="invoice",
                extracted_data_json=extracted_data,
                product_alias_matches_json=alias_matches,
                status="pending_review",
            )
            db.add(staging)
            db.commit()

            # Format and send preview message
            lines = ["📄 Invoice Preview:\n"]
            lines.append(f"Supplier: {extracted_data.get('supplier_name', 'N/A')}")
            lines.append(f"Invoice Number: {extracted_data.get('invoice_number', 'N/A')}")
            lines.append(f"Date: {extracted_data.get('invoice_date', 'N/A')}")
            lines.append(f"Currency: {extracted_data.get('currency', 'N/A')}")
            lines.append(f"Total: {extracted_data.get('total', 'N/A')}")
            lines.append("\nLine Items:")
            for i, item in enumerate(extracted_data.get("line_items", []), 1):
                lines.append(
                    f"  {i}. {item.get('description', 'N/A')} - "
                    f"{item.get('quantity', 'N/A')} {item.get('unit', '')} @ "
                    f"{item.get('unit_price', 'N/A')} = {item.get('line_total', 'N/A')}"
                )
            lines.append(
                f"\n\nReview the data above. Tell me if anything needs changing, or say /confirm to save."
            )
            preview_text = "\n".join(lines)

            send_message(chat_id=chat_id, text=preview_text, settings=settings)

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
            send_message(
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your invoice file. Error: {str(exc)}",
                settings=settings,
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

    try:
        with worker_db_session() as db:
            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)
            
            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            # Extract price list data
            extracted_data = extract_price_list_data(file_bytes, mime_type, settings, filename)

            # Find or create supplier if not provided
            supplier_uuid = None
            if supplier_id:
                supplier_uuid = _parse_uuid(supplier_id)
            else:
                # Try to find supplier by name from extracted data
                supplier_name = extracted_data.get("supplier_name", "").strip()
                if supplier_name:
                    supplier = db.scalar(
                        select(Suppliers).where(
                            Suppliers.restaurant_id == restaurant_uuid,
                            Suppliers.name.ilike(supplier_name),
                            Suppliers.is_active == True,
                        )
                    )
                    if supplier:
                        supplier_uuid = supplier.id

            # Match product aliases for items
            supplier_names = [item.get("name", "") for item in extracted_data.get("items", [])]
            alias_matches = match_product_aliases(restaurant_uuid, supplier_names, db, settings)

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

            # Create staging record
            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=document.id,
                processing_type="price_list",
                extracted_data_json=extracted_data,
                product_alias_matches_json=alias_matches,
                status="pending_review",
            )
            db.add(staging)
            db.commit()

            # Format and send preview message
            lines = ["📋 Price List Preview:\n"]
            lines.append(f"Supplier: {extracted_data.get('supplier_name', 'N/A')}")
            lines.append(f"Currency: {extracted_data.get('currency', 'N/A')}")
            lines.append(f"Items: {len(extracted_data.get('items', []))}")
            lines.append("\nItems:")
            for i, item in enumerate(extracted_data.get("items", []), 1):
                lines.append(
                    f"  {i}. {item.get('name', 'N/A')} - "
                    f"{item.get('price', 'N/A')} {item.get('currency', '')} per {item.get('unit', '')}"
                )
            lines.append(
                f"\n\nReview the data above. Tell me if anything needs changing, or say /confirm to save."
            )
            preview_text = "\n".join(lines)

            send_message(chat_id=chat_id, text=preview_text, settings=settings)

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
            send_message(
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your price list file. Error: {str(exc)}",
                settings=settings,
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

    try:
        with worker_db_session() as db:
            # Download file
            file_bytes = get_file_bytes(file_id=file_id, settings=settings)
            
            # Get actual mime type and filename from Telegram message
            telegram_msg = db.scalar(
                select(TelegramMessages)
                .where(TelegramMessages.file_id == file_id)
                .order_by(TelegramMessages.received_at.desc())
                .limit(1)
            )
            mime_type = telegram_msg.mime if telegram_msg and telegram_msg.mime else "image/jpeg"
            if not mime_type:
                mime_type = "image/jpeg"  # Fallback
            filename = telegram_msg.filename if telegram_msg else None

            # Extract inventory data
            extracted_data = extract_inventory_data(file_bytes, mime_type, settings, filename)

            # Match product aliases for detected items
            supplier_names = [
                item.get("product_name", "") for item in extracted_data.get("items", [])
            ]
            alias_matches = match_product_aliases(restaurant_uuid, supplier_names, db, settings)

            # Create staging record (no document for inventory photos)
            staging = FileProcessingStaging(
                restaurant_id=restaurant_uuid,
                user_id=user_uuid,
                document_id=None,
                processing_type="inventory",
                extracted_data_json=extracted_data,
                product_alias_matches_json=alias_matches,
                status="pending_review",
            )
            db.add(staging)
            db.commit()

            # Format and send preview message
            lines = ["📸 Inventory Photo Preview:\n"]
            lines.append(f"Items Detected: {len(extracted_data.get('items', []))}")
            lines.append("\nItems:")
            for i, item in enumerate(extracted_data.get("items", []), 1):
                lines.append(
                    f"  {i}. {item.get('product_name', 'N/A')} - "
                    f"{item.get('quantity', 'N/A')} {item.get('unit', '')}"
                )
            lines.append(
                f"\n\nReview the data above. Tell me if anything needs changing, or say /confirm to save."
            )
            preview_text = "\n".join(lines)

            send_message(chat_id=chat_id, text=preview_text, settings=settings)

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
            send_message(
                chat_id=chat_id,
                text=f"Sorry, I couldn't process your inventory photo. Error: {str(exc)}",
                settings=settings,
            )
        except Exception:
            pass
        raise
