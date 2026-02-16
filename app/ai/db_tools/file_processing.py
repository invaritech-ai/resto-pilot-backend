"""
File processing tools for invoices, price lists, and inventory photos.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.file_processing_staging import FileProcessingStaging
from app.workers.celery_types import CeleryApplyAsync

from .base import has_restaurant_access

logger = logging.getLogger(__name__)


def create_file_processing_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
    chat_id: int | None = None,
    session_id: uuid.UUID | None = None,
) -> dict[str, Tool]:
    """Create file processing tools."""

    def process_invoice_file(args: dict[str, Any]) -> str:
        """Enqueue invoice file processing. The file will be processed and you'll get a preview to review."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        file_id = args.get("file_id", "").strip()
        if not file_id:
            return "Error: file_id is required."

        supplier_id_str = args.get("supplier_id", "").strip()
        supplier_id = uuid.UUID(supplier_id_str) if supplier_id_str else None

        if not chat_id:
            return "Error: chat_id is required for file processing."

        # Enqueue async task
        from app.workers.tasks import process_invoice_file_task  # imported lazily

        cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
            kwargs={
                "restaurant_id": str(restaurant_id),
                "file_id": file_id,
                "supplier_id": str(supplier_id) if supplier_id else None,
                "chat_id": chat_id,
                "user_id": str(user_id),
                "session_id": str(session_id) if session_id else None,
            },
            countdown=0.0,
        )

        return "I'm processing your invoice. I'll show you what I found for review."

    def process_price_list_file(args: dict[str, Any]) -> str:
        """Enqueue price list file processing. The file will be processed and you'll get a preview to review."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        file_id = args.get("file_id", "").strip()
        if not file_id:
            return "Error: file_id is required."

        supplier_id_str = args.get("supplier_id", "").strip()
        supplier_id = uuid.UUID(supplier_id_str) if supplier_id_str else None

        if not chat_id:
            return "Error: chat_id is required for file processing."

        # Enqueue async task
        from app.workers.tasks import process_price_list_file_task  # imported lazily

        cast(CeleryApplyAsync, process_price_list_file_task).apply_async(
            kwargs={
                "restaurant_id": str(restaurant_id),
                "file_id": file_id,
                "supplier_id": str(supplier_id) if supplier_id else None,
                "chat_id": chat_id,
                "user_id": str(user_id),
                "session_id": str(session_id) if session_id else None,
            },
            countdown=0.0,
        )

        return "I'm processing your price list. I'll show you what I found for review."

    def process_inventory_photo(args: dict[str, Any]) -> str:
        """Enqueue inventory photo processing. The photo will be analyzed and you'll get a preview to review."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        file_id = args.get("file_id", "").strip()
        if not file_id:
            return "Error: file_id is required."

        if not chat_id:
            return "Error: chat_id is required for file processing."

        # Enqueue async task
        from app.workers.tasks import process_inventory_photo_task  # imported lazily

        cast(CeleryApplyAsync, process_inventory_photo_task).apply_async(
            kwargs={
                "restaurant_id": str(restaurant_id),
                "file_id": file_id,
                "chat_id": chat_id,
                "user_id": str(user_id),
                "session_id": str(session_id) if session_id else None,
            },
            countdown=0.0,
        )

        return (
            "I'm analyzing your inventory photo. I'll show you what I found for review."
        )

    def review_file_processing(args: dict[str, Any]) -> str:
        """Show extracted data from file processing for review."""
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            return "Error: staging_id is required."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: File processing record not found."

        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this file processing record."

        if staging.status not in (
            "pending_review",
            "awaiting_supplier",
            "awaiting_currency",
        ):
            return f"Error: This record is already {staging.status}. Cannot review."

        # Format the extracted data nicely
        extracted_data = staging.extracted_data_json
        processing_type = staging.processing_type

        if processing_type == "invoice":
            lines = ["📄 Invoice Preview:\n"]
            supplier_name = (
                extracted_data.get("supplier_name", "").strip()
                if extracted_data.get("supplier_name")
                else ""
            )
            currency = (
                extracted_data.get("currency", "").strip()
                if extracted_data.get("currency")
                else ""
            )
            supplier_display = supplier_name or "⚠️ MISSING"
            currency_display = currency or "⚠️ MISSING"
            lines.append(f"Supplier: {supplier_display}")
            lines.append(
                f"Invoice Number: {extracted_data.get('invoice_number', 'N/A')}"
            )
            lines.append(f"Date: {extracted_data.get('invoice_date', 'N/A')}")
            lines.append(f"Currency: {currency_display}")
            lines.append(f"Total: {extracted_data.get('total', 'N/A')}")
            lines.append("\nLine Items:")
            for i, item in enumerate(extracted_data.get("line_items", []), 1):
                desc = item.get("description_raw", item.get("description", "N/A"))
                qty = item.get("quantity", "N/A")
                unit = item.get("unit", "")
                price = item.get("unit_price", "N/A")
                total = item.get("line_total", "N/A")
                source_info = ""
                if item.get("source_page"):
                    source_info = f" [Page {item['source_page']}]"
                lines.append(
                    f"  {i}. {desc} - {qty} {unit} @ {price} = {total}{source_info}"
                )
        elif processing_type == "price_list":
            lines = ["📋 Price List Preview:\n"]
            supplier_name = (
                extracted_data.get("supplier_name", "").strip()
                if extracted_data.get("supplier_name")
                else ""
            )
            currency = (
                extracted_data.get("currency", "").strip()
                if extracted_data.get("currency")
                else ""
            )
            supplier_display = supplier_name or "⚠️ MISSING"
            currency_display = currency or "⚠️ MISSING"
            lines.append(f"Supplier: {supplier_display}")
            lines.append(f"Currency: {currency_display}")
            lines.append(f"Items: {len(extracted_data.get('items', []))}")
            lines.append("\nItems:")
            for i, item in enumerate(extracted_data.get("items", []), 1):
                name = item.get("supplier_name_raw", item.get("name", "N/A"))
                price = item.get("price", "N/A")
                item_currency = item.get("currency", currency_display)
                unit_basis = item.get("unit_basis", "")
                pack_size = item.get("pack_size_text", "")
                min_order_qty = item.get("min_order_qty")
                source_info = ""
                if item.get("source_page"):
                    source_info = f" [Page {item['source_page']}]"

                item_line = f"  {i}. {name}"
                if pack_size:
                    item_line += f" (pack_size_text: {pack_size})"
                if unit_basis:
                    item_line += f" (unit_basis: {unit_basis})"
                if min_order_qty is not None:
                    item_line += f" (min_order_qty: {min_order_qty})"
                item_line += f" - {price} {item_currency}{source_info}"
                lines.append(item_line)
        elif processing_type == "inventory":
            lines = ["📸 Inventory Photo Preview:\n"]
            lines.append(f"Items Detected: {len(extracted_data.get('items', []))}")
            lines.append("\nItems:")
            for i, item in enumerate(extracted_data.get("items", []), 1):
                product_name = item.get("product_name", "N/A")
                quantity = item.get("quantity", "N/A")
                unit = item.get("unit", "")
                unit_cost = item.get("unit_cost")
                status_val = item.get("status")
                source_info = ""
                if item.get("source_page"):
                    source_info = f" [Page {item['source_page']}]"

                item_line = f"  {i}. {product_name} - {quantity} {unit}"
                if unit_cost is not None:
                    item_line += f" (unit_cost: {unit_cost})"
                if status_val:
                    item_line += f" (status: {status_val})"
                item_line += source_info
                lines.append(item_line)
        else:
            lines = [f"Preview for {processing_type}:\n"]
            lines.append(json.dumps(extracted_data, indent=2))

        lines.append(
            "\n\nReview the data above. Tell me if anything needs changing, or say /confirm to save."
        )
        return "\n".join(lines)

    def update_missing_field(args: dict[str, Any]) -> str:
        """Update a missing supplier or currency field and show preview."""
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            return "Error: staging_id is required."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: File processing record not found."

        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this file processing record."

        if staging.status not in ("awaiting_supplier", "awaiting_currency"):
            return f"Error: This record is not awaiting a missing field. Current status: {staging.status}"

        field_value = args.get("value", "").strip()
        if not field_value:
            return "Error: value is required."

        # Update extracted_data_json
        extracted_data = staging.extracted_data_json.copy()

        if staging.status == "awaiting_supplier":
            extracted_data["supplier_name"] = field_value
            # Check if currency is also missing
            currency = (
                extracted_data.get("currency", "").strip()
                if extracted_data.get("currency")
                else ""
            )
            if not currency:
                staging.status = "awaiting_currency"
            else:
                staging.status = "pending_review"
        elif staging.status == "awaiting_currency":
            extracted_data["currency"] = field_value
            staging.status = "pending_review"

        staging.extracted_data_json = extracted_data
        try:
            db.commit()

            # If status is now pending_review, return preview
            if staging.status == "pending_review":
                # Use review_file_processing logic to format preview
                processing_type = staging.processing_type
                if processing_type == "invoice":
                    lines = ["📄 Invoice Preview:\n"]
                    supplier_display = extracted_data.get("supplier_name", "N/A")
                    currency_display = extracted_data.get("currency", "N/A")
                    lines.append(f"Supplier: {supplier_display}")
                    lines.append(
                        f"Invoice Number: {extracted_data.get('invoice_number', 'N/A')}"
                    )
                    lines.append(f"Date: {extracted_data.get('invoice_date', 'N/A')}")
                    lines.append(f"Currency: {currency_display}")
                    lines.append(f"Total: {extracted_data.get('total', 'N/A')}")
                    lines.append("\nLine Items:")
                    for i, item in enumerate(extracted_data.get("line_items", []), 1):
                        desc = item.get(
                            "description_raw", item.get("description", "N/A")
                        )
                        qty = item.get("quantity", "N/A")
                        unit = item.get("unit", "")
                        price = item.get("unit_price", "N/A")
                        total = item.get("line_total", "N/A")
                        source_info = ""
                        if item.get("source_page"):
                            source_info = f" [Page {item['source_page']}]"
                        lines.append(
                            f"  {i}. {desc} - {qty} {unit} @ {price} = {total}{source_info}"
                        )
                elif processing_type == "price_list":
                    lines = ["📋 Price List Preview:\n"]
                    supplier_display = extracted_data.get("supplier_name", "N/A")
                    currency_display = extracted_data.get("currency", "N/A")
                    lines.append(f"Supplier: {supplier_display}")
                    lines.append(f"Currency: {currency_display}")
                    lines.append(f"Items: {len(extracted_data.get('items', []))}")
                    lines.append("\nItems:")
                    for i, item in enumerate(extracted_data.get("items", []), 1):
                        name = item.get("supplier_name_raw", item.get("name", "N/A"))
                        price = item.get("price", "N/A")
                        item_currency = item.get("currency", currency_display)
                        unit_basis = item.get("unit_basis", "")
                        pack_size = item.get("pack_size_text", "")
                        min_order_qty = item.get("min_order_qty")
                        source_info = ""
                        if item.get("source_page"):
                            source_info = f" [Page {item['source_page']}]"

                        item_line = f"  {i}. {name}"
                        if pack_size:
                            item_line += f" (pack_size_text: {pack_size})"
                        if unit_basis:
                            item_line += f" (unit_basis: {unit_basis})"
                        if min_order_qty is not None:
                            item_line += f" (min_order_qty: {min_order_qty})"
                        item_line += f" - {price} {item_currency}{source_info}"
                        lines.append(item_line)

                lines.append(
                    "\n\nReview the data above. Tell me if anything needs changing, or say /confirm to save."
                )
                return "\n".join(lines)
            else:
                # Still waiting for another field
                if staging.status == "awaiting_currency":
                    return (
                        "Updated supplier. What currency is this in? (e.g., USD, EUR)"
                    )
                return f"Updated {field_value}. Status: {staging.status}"
        except Exception as e:
            db.rollback()
            logger.exception("update_missing_field_failed")
            return f"Error updating missing field: {str(e)}"

    def update_file_processing_data(args: dict[str, Any]) -> str:
        """Update a specific field in the extracted data before confirming."""
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            return "Error: staging_id is required."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: File processing record not found."

        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this file processing record."

        if staging.status != "pending_review":
            return f"Error: This record is already {staging.status}. Cannot update."

        field_path = args.get("field_path", "").strip()
        new_value = args.get("new_value")

        if not field_path:
            return "Error: field_path is required (e.g., 'supplier_name', 'line_items.0.quantity')."

        # Update nested field in JSON
        extracted_data = staging.extracted_data_json.copy()
        path_parts = field_path.split(".")
        current = extracted_data

        # Navigate to the parent of the target field
        for part in path_parts[:-1]:
            if isinstance(part, str) and part.isdigit():
                part = int(part)
            if not isinstance(current, (dict, list)):
                return (
                    f"Error: Invalid path '{field_path}' - '{part}' is not a dict/list."
                )
            if isinstance(current, list):
                if not isinstance(part, int) or part >= len(current):
                    return f"Error: Invalid path '{field_path}' - index {part} out of range."
                current = current[part]
            else:
                if part not in current:
                    return f"Error: Invalid path '{field_path}' - '{part}' not found."
                current = current[part]

        # Update the target field
        final_key = path_parts[-1]
        if isinstance(current, list):
            if not final_key.isdigit():
                return f"Error: Invalid path '{field_path}' - final part must be an index for lists."
            idx = int(final_key)
            if idx >= len(current):
                return f"Error: Invalid path '{field_path}' - index {idx} out of range."
            current[idx] = new_value
        else:
            current[final_key] = new_value

        staging.extracted_data_json = extracted_data
        try:
            db.commit()
            return f"Updated {field_path} to {new_value}. Use review_file_processing to see the updated data."
        except Exception as e:
            db.rollback()
            logger.exception("update_file_processing_data_failed")
            return f"Error updating data: {str(e)}"

    def check_file_processing_status(args: dict[str, Any]) -> str:
        """
        Check the status of a file processing job.

        User can ask: "How's my invoice processing going?" or "What's the status of my file?"
        """
        from app.db.queries.file_processing import (
            get_processing_status,
            get_user_processing_jobs,
        )

        run_id_str = args.get("run_id", "").strip()

        try:
            if run_id_str:
                # Check specific run
                run_id = uuid.UUID(run_id_str)
                status = get_processing_status(run_id, db)
            else:
                # Get latest job for current user
                jobs = get_user_processing_jobs(user_id, db, limit=1)
                if not jobs:
                    return "No file processing jobs found for your account."
                status = jobs[0]

            # Format response based on status
            if status["status"] == "completed":
                msg = "✅ Your file processing is complete!\n\n"
                msg += f"Pages processed: {status['pages_processed']}/{status['pages_total']}\n"
                msg += f"Time taken: {status['elapsed_seconds']} seconds\n\n"
                msg += "Use review_file_processing to see the extracted data."
                return msg

            elif status["status"] == "failed":
                msg = "❌ File processing failed.\n\n"
                if status["error_message"]:
                    msg += f"Error: {status['error_message']}\n\n"
                msg += "Please try uploading the file again."
                return msg

            elif status["status"] == "processing":
                msg = "⏳ Your file is being processed...\n\n"
                msg += f"Progress: {status['progress_percentage']:.0f}% complete\n"
                msg += f"Pages: {status['pages_processed']}/{status['pages_total']}\n"
                msg += f"Current stage: {status['current_stage']}\n"

                if status["estimated_seconds_remaining"]:
                    eta_minutes = status["estimated_seconds_remaining"] // 60
                    msg += f"\nEstimated time remaining: ~{eta_minutes} minutes"

                return msg

            else:
                # Pending or other status
                msg = f"Status: {status['status']}\n"
                if status['pages_total']:
                    msg += f"Pages: {status['pages_processed']}/{status['pages_total']}\n"
                return msg

        except ValueError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            logger.exception("check_file_processing_status_failed")
            return f"Error checking status: {str(e)}"

    def confirm_file_processing(args: dict[str, Any]) -> str:
        """Confirm and write extracted data to final tables. Both owners and staff can confirm (attribution is tracked)."""
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            return "Error: staging_id is required."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: File processing record not found."

        if not staging.restaurant_id:
            return "Error: This record is missing restaurant_id."

        # Allow both owners and staff to confirm - attribution is tracked via authorized_by_user_id
        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this restaurant."

        if staging.status != "pending_review":
            return f"Error: This record is already {staging.status}. Cannot confirm."

        try:
            from app.db.queries.file_processing_confirm import (
                FileProcessingConfirmError,
                confirm_file_processing_staging,
            )

            result = confirm_file_processing_staging(
                db=db,
                staging=staging,
                owner_user_id=staging.user_id,
                authorized_by_user_id=user_id,
            )
            processing_type = result.get("processing_type")
            summary = result.get("summary") or {}

            if processing_type == "invoice":
                return (
                    "Confirmed! "
                    f"Invoice created: {summary.get('invoice_number')}, "
                    f"{summary.get('line_items_created')} line items, "
                    f"Total: {summary.get('total')} {summary.get('currency')}"
                )
            if processing_type == "price_list":
                return (
                    "Confirmed! "
                    f"Price list saved: {summary.get('prices_created')} prices "
                    f"({summary.get('items_created')} new items, {summary.get('items_updated')} updated)"
                )
            if processing_type == "inventory":
                suffix = ""
                if summary.get("skipped_no_product"):
                    suffix = (
                        f", {summary.get('skipped_no_product')} items skipped (no matching product)"
                    )
                return (
                    f"Confirmed! Inventory saved: {summary.get('batches_created')} batches created{suffix}"
                )

            return "Confirmed!"
        except FileProcessingConfirmError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            logger.exception("confirm_file_processing_failed")
            return f"Error confirming file processing: {str(e)}"

    def correct_invoice_line_item(args: dict[str, Any]) -> str:
        """Correct a line item in an invoice using natural language like 'Onion is 4kg, 10, $40'."""
        from app.context.user import get_context_from_db

        # Get staging_id from context or args
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            context = get_context_from_db(db, user_id)
            if context and context.last_file:
                staging_id_str = context.last_file.get("staging_id", "")

        if not staging_id_str:
            return "Error: No invoice found in context. Please specify staging_id."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: Invoice not found."

        if staging.status != "pending_review":
            return f"Error: This invoice is already {staging.status}. Cannot correct."

        if staging.processing_type != "invoice":
            return "Error: This tool only works with invoices."

        item_name = args.get("item_name", "").strip().lower()
        if not item_name:
            return "Error: item_name is required (e.g., 'onion', 'fennel')."

        # Parse the correction data
        quantity_str = args.get("quantity")
        unit_price = args.get("unit_price")
        line_total = args.get("line_total")

        # Find the line item by name (fuzzy match)
        extracted_data = staging.extracted_data_json.copy()
        line_items = extracted_data.get("line_items", [])

        item_index = None
        for i, item in enumerate(line_items):
            desc = (item.get("description_raw") or "").lower()
            if item_name in desc or desc in item_name:
                item_index = i
                break

        if item_index is None:
            return f"Error: Item '{item_name}' not found in invoice. Available items: {', '.join([item.get('description_raw', '?') for item in line_items[:5]])}..."

        # Parse quantity_str like "4kg" into quantity and unit
        quantity = None
        unit = None
        if quantity_str:
            import re
            match = re.match(r'^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$', str(quantity_str).strip())
            if match:
                quantity = float(match.group(1))
                unit = match.group(2) or None

        # Update the line item
        if quantity is not None:
            line_items[item_index]["quantity"] = quantity
        if unit is not None:
            line_items[item_index]["unit"] = unit
        if unit_price is not None:
            line_items[item_index]["unit_price"] = float(unit_price)
        if line_total is not None:
            line_items[item_index]["line_total"] = float(line_total)

        extracted_data["line_items"] = line_items
        staging.extracted_data_json = extracted_data

        try:
            db.commit()
            updated_item = line_items[item_index]
            return f"✅ Corrected '{updated_item.get('description_raw')}': quantity={updated_item.get('quantity')} {updated_item.get('unit') or ''}, unit_price={updated_item.get('unit_price')}, total={updated_item.get('line_total')}"
        except Exception as e:
            db.rollback()
            logger.exception("correct_invoice_line_item_failed")
            return f"Error: {str(e)}"

    return {
        "process_invoice_file": Tool(
            name="process_invoice_file",
            description="Process an invoice file. The file will be analyzed and you'll get a preview to review before saving.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "file_id": {
                        "type": "string",
                        "description": "Telegram file_id of the invoice file.",
                    },
                    "supplier_id": {
                        "type": "string",
                        "description": "Supplier UUID (optional, will be inferred if not provided).",
                    },
                },
                "required": ["restaurant_id", "file_id"],
                "additionalProperties": False,
            },
            handler=process_invoice_file,
        ),
        "process_price_list_file": Tool(
            name="process_price_list_file",
            description="Process a price list file. The file will be analyzed and you'll get a preview to review before saving.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "file_id": {
                        "type": "string",
                        "description": "Telegram file_id of the price list file.",
                    },
                    "supplier_id": {
                        "type": "string",
                        "description": "Supplier UUID (optional, will be inferred if not provided).",
                    },
                },
                "required": ["restaurant_id", "file_id"],
                "additionalProperties": False,
            },
            handler=process_price_list_file,
        ),
        "process_inventory_photo": Tool(
            name="process_inventory_photo",
            description="Process an inventory photo. The photo will be analyzed and you'll get a preview to review before saving.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "file_id": {
                        "type": "string",
                        "description": "Telegram file_id of the inventory photo.",
                    },
                },
                "required": ["restaurant_id", "file_id"],
                "additionalProperties": False,
            },
            handler=process_inventory_photo,
        ),
        "review_file_processing": Tool(
            name="review_file_processing",
            description="Show extracted data from file processing for review.",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID.",
                    },
                },
                "required": ["staging_id"],
                "additionalProperties": False,
            },
            handler=review_file_processing,
        ),
        "update_file_processing_data": Tool(
            name="update_file_processing_data",
            description="Update a specific field in the extracted data before confirming. Use dot notation for nested fields (e.g., 'supplier_name', 'line_items.0.quantity').",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID.",
                    },
                    "field_path": {
                        "type": "string",
                        "description": "Path to the field to update (e.g., 'supplier_name', 'line_items.0.quantity').",
                    },
                    "new_value": {
                        "description": "New value for the field.",
                    },
                },
                "required": ["staging_id", "field_path", "new_value"],
                "additionalProperties": False,
            },
            handler=update_file_processing_data,
        ),
        "update_missing_field": Tool(
            name="update_missing_field",
            description="Update a missing supplier or currency field when the system asks for it. Use this when the user responds to a missing field question.",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The supplier name or currency value provided by the user.",
                    },
                },
                "required": ["staging_id", "value"],
                "additionalProperties": False,
            },
            handler=update_missing_field,
        ),
        "check_file_processing_status": Tool(
            name="check_file_processing_status",
            description="Check the status of a file processing job. Use this when user asks 'how's my file?', 'what's the status?', or similar questions.",
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The file processing run UUID (optional - if not provided, returns latest job for user).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=check_file_processing_status,
        ),
        "confirm_file_processing": Tool(
            name="confirm_file_processing",
            description="Confirm and write extracted data to final tables. Both owners and staff can confirm (attribution is tracked).",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID.",
                    },
                },
                "required": ["staging_id"],
                "additionalProperties": False,
            },
            handler=confirm_file_processing,
        ),
        "correct_invoice_line_item": Tool(
            name="correct_invoice_line_item",
            description="Correct a line item in an invoice with natural language like 'Onion is 4kg, 10, $40'. Automatically finds the item by name and updates quantity, unit_price, and line_total.",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID (optional, uses context if omitted).",
                    },
                    "item_name": {
                        "type": "string",
                        "description": "The product name to correct (e.g., 'onion', 'fennel'). Case-insensitive fuzzy match.",
                    },
                    "quantity": {
                        "type": "string",
                        "description": "Quantity with optional unit (e.g., '4kg', '2.5', '10pcs'). Will be parsed into quantity and unit.",
                    },
                    "unit_price": {
                        "type": "number",
                        "description": "Unit price per unit (e.g., 10 for $10 per kg).",
                    },
                    "line_total": {
                        "type": "number",
                        "description": "Total amount for this line (e.g., 40 for $40).",
                    },
                },
                "required": ["item_name"],
                "additionalProperties": False,
            },
            handler=correct_invoice_line_item,
        ),
    }
