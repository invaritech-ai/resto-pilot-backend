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

        # Allow both owners and staff to confirm - attribution is tracked via authorized_by_user_id
        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this restaurant."

        if staging.status != "pending_review":
            return f"Error: This record is already {staging.status}. Cannot confirm."

        # Write to final tables based on processing type
        import datetime as dt

        extracted_data = staging.extracted_data_json
        processing_type = staging.processing_type

        # Helper function to parse dates
        def parse_date(date_str: str | None) -> dt.datetime | None:
            if not date_str:
                return None
            try:
                # Try ISO format first
                date_str_clean = date_str.replace("Z", "+00:00")
                return dt.datetime.fromisoformat(date_str_clean)
            except (ValueError, AttributeError):
                # Try simple date formats
                try:
                    # Try YYYY-MM-DD format
                    if len(date_str) >= 10:
                        date_part = date_str[:10]
                        return dt.datetime.strptime(date_part, "%Y-%m-%d").replace(
                            tzinfo=dt.UTC
                        )
                except ValueError:
                    pass
                return None

        try:
            if processing_type == "invoice":
                from app.db.models.documents import Documents
                from app.db.models.invoices import Invoices
                from app.db.models.invoice_line_items import InvoiceLineItems
                from app.db.models.suppliers import Suppliers

                # Get document (should already exist from processing task)
                document = None
                if staging.document_id:
                    document = db.get(Documents, staging.document_id)

                if not document:
                    return "Error: Document record not found. The file processing may not have completed correctly."

                # Find or create supplier by name
                supplier_name = extracted_data.get("supplier_name", "").strip()
                if not supplier_name:
                    return "Error: Supplier name is required for invoice processing."

                supplier = db.scalar(
                    select(Suppliers).where(
                        Suppliers.restaurant_id == staging.restaurant_id,
                        Suppliers.name.ilike(supplier_name),
                        Suppliers.is_active,
                    )
                )

                if not supplier:
                    # Create new supplier
                    supplier = Suppliers(
                        restaurant_id=staging.restaurant_id,
                        name=supplier_name,
                        currency=extracted_data.get("currency", "USD"),
                        is_active=True,
                    )
                    db.add(supplier)
                    db.flush()

                supplier_id = supplier.id

                # Update document with supplier_id
                document.supplier_id = supplier_id

                invoice_date = parse_date(
                    extracted_data.get("invoice_date")
                ) or dt.datetime.now(dt.UTC)
                due_date = parse_date(extracted_data.get("due_date"))

                # Create invoice
                invoice = Invoices(
                    restaurant_id=staging.restaurant_id,
                    supplier_id=supplier_id,
                    invoice_number=extracted_data.get("invoice_number", ""),
                    invoice_date=invoice_date,
                    due_date=due_date,
                    currency=extracted_data.get("currency", "USD"),
                    subtotal=float(extracted_data.get("subtotal", 0)),
                    tax=float(extracted_data.get("tax", 0)),
                    total=float(extracted_data.get("total", 0)),
                    document_id=document.id,
                    status="received",
                    authorized_by_user_id=user_id,
                )
                db.add(invoice)
                db.flush()

                # Create line items
                for item in extracted_data.get("line_items", []):
                    line_item = InvoiceLineItems(
                        invoice_id=invoice.id,
                        supplier_id=supplier_id,
                        description_raw=item.get(
                            "description_raw", item.get("description", "")
                        ),
                        quantity=float(item.get("quantity", 0)),
                        unit=item.get("unit", ""),
                        unit_price=float(item.get("unit_price", 0)),
                        line_total=float(item.get("line_total", 0)),
                        currency=extracted_data.get("currency", "USD"),
                        tax_amount=float(item.get("tax_amount", 0)),
                    )
                    db.add(line_item)

                summary = f"Invoice created: {invoice.invoice_number}, {len(extracted_data.get('line_items', []))} line items, Total: {invoice.total} {invoice.currency}"

            elif processing_type == "price_list":
                from app.db.models.documents import Documents
                from app.db.models.suppliers import Suppliers
                from app.db.models.supplier_items import SupplierItems
                from app.db.models.supplier_prices import SupplierPrices

                # Get document (should already exist from processing task)
                document = None
                if staging.document_id:
                    document = db.get(Documents, staging.document_id)

                if not document:
                    return "Error: Document record not found. The file processing may not have completed correctly."

                # Find or create supplier by name
                supplier_name = extracted_data.get("supplier_name", "").strip()
                if not supplier_name:
                    return "Error: Supplier name is required for price list processing."

                supplier = db.scalar(
                    select(Suppliers).where(
                        Suppliers.restaurant_id == staging.restaurant_id,
                        Suppliers.name.ilike(supplier_name),
                        Suppliers.is_active,
                    )
                )

                if not supplier:
                    # Create new supplier with contact info if available
                    supplier = Suppliers(
                        restaurant_id=staging.restaurant_id,
                        name=supplier_name,
                        contact_name=extracted_data.get("contact_name"),
                        contact_email=extracted_data.get("contact_email"),
                        contact_phone=extracted_data.get("contact_phone"),
                        currency=extracted_data.get("currency", "USD"),
                        is_active=True,
                    )
                    db.add(supplier)
                    db.flush()
                else:
                    # Update contact info if provided and not already set
                    if extracted_data.get("contact_name") and not supplier.contact_name:
                        supplier.contact_name = extracted_data.get("contact_name")
                    if (
                        extracted_data.get("contact_email")
                        and not supplier.contact_email
                    ):
                        supplier.contact_email = extracted_data.get("contact_email")
                    if (
                        extracted_data.get("contact_phone")
                        and not supplier.contact_phone
                    ):
                        supplier.contact_phone = extracted_data.get("contact_phone")
                    if extracted_data.get("currency") and not supplier.currency:
                        supplier.currency = extracted_data.get("currency")

                supplier_id = supplier.id

                # Update document with supplier_id
                document.supplier_id = supplier_id

                # Parse effective date
                effective_date = parse_date(
                    extracted_data.get("effective_date")
                ) or dt.datetime.now(dt.UTC)

                # Create supplier items and prices
                items_created = 0
                prices_created = 0

                for item_data in extracted_data.get("items", []):
                    supplier_name_raw = item_data.get(
                        "supplier_name_raw", item_data.get("name", "")
                    )
                    if not supplier_name_raw:
                        continue

                    # Check if item already exists (de-duplicate by supplier_name_raw + supplier_id)
                    existing_item = db.scalar(
                        select(SupplierItems).where(
                            SupplierItems.supplier_id == supplier_id,
                            SupplierItems.supplier_name_raw.ilike(supplier_name_raw),
                            SupplierItems.status == "active",
                        )
                    )

                    if existing_item:
                        supplier_item = existing_item
                    else:
                        # Create new supplier item WITHOUT product_id
                        # We store raw names and search when user asks "who has item X?"
                        supplier_item = SupplierItems(
                            supplier_id=supplier_id,
                            product_id=None,  # No product matching - store raw names only
                            supplier_sku=item_data.get("supplier_sku"),
                            supplier_name_raw=supplier_name_raw,
                            pack_size_text=item_data.get("pack_size_text"),
                            unit_basis=item_data.get("unit_basis"),
                            min_order_qty=float(item_data.get("min_order_qty", 0))
                            if item_data.get("min_order_qty") is not None
                            else None,
                            status="active",
                            source_document_id=document.id,
                        )
                        db.add(supplier_item)
                        db.flush()
                        items_created += 1

                    # Create price entry
                    valid_from = (
                        parse_date(item_data.get("valid_from")) or effective_date
                    )
                    valid_to = parse_date(item_data.get("valid_to"))

                    price = SupplierPrices(
                        supplier_item_id=supplier_item.id,
                        price=float(item_data.get("price", 0)),
                        currency=item_data.get(
                            "currency", extracted_data.get("currency", "USD")
                        ),
                        price_type=item_data.get("price_type", "standard"),
                        valid_from=valid_from,
                        valid_to=valid_to,
                        min_qty=float(item_data.get("min_qty", 0))
                        if item_data.get("min_qty") is not None
                        else None,
                        source_document_id=document.id,
                    )
                    db.add(price)
                    prices_created += 1

                summary = f"Price list confirmed: {items_created} items, {prices_created} prices for {supplier_name}"

            elif processing_type == "inventory":
                from app.db.models.inventory_batches import InventoryBatches
                from app.db.models.products import Products
                from app.db.models.suppliers import Suppliers

                # For inventory, we need product_id - user will need to match products
                # For now, we'll create batches but they need product_id
                # TODO: Consider making product_id optional or handling unmatched items differently

                batches_created = 0
                skipped_no_product = 0

                for item_data in extracted_data.get("items", []):
                    product_name = item_data.get("product_name", "").strip()
                    if not product_name:
                        continue

                    # Try to find product by name
                    product = db.scalar(
                        select(Products).where(
                            Products.restaurant_id == staging.restaurant_id,
                            (
                                Products.name_en.ilike(product_name)
                                | Products.name_local.ilike(product_name)
                            ),
                            Products.is_active,
                        )
                    )

                    if not product:
                        skipped_no_product += 1
                        continue  # Skip items without matching product

                    # Find or use default supplier (inventory may not have supplier info)
                    supplier_id = None
                    if item_data.get("supplier_id"):
                        supplier_id = uuid.UUID(item_data.get("supplier_id"))
                    else:
                        # Try to find supplier by name if provided
                        supplier_name = extracted_data.get("supplier_name")
                        if supplier_name:
                            supplier = db.scalar(
                                select(Suppliers).where(
                                    Suppliers.restaurant_id == staging.restaurant_id,
                                    Suppliers.name.ilike(supplier_name),
                                    Suppliers.is_active,
                                )
                            )
                            if supplier:
                                supplier_id = supplier.id

                    if not supplier_id:
                        skipped_no_product += 1
                        continue  # Need supplier for inventory batch

                    received_date = parse_date(
                        item_data.get("received_date")
                    ) or dt.datetime.now(dt.UTC)
                    expiry_date = parse_date(item_data.get("expiry_date"))

                    batch = InventoryBatches(
                        restaurant_id=staging.restaurant_id,
                        product_id=product.id,
                        supplier_id=supplier_id,
                        quantity=float(item_data.get("quantity", 0)),
                        unit=item_data.get("unit", ""),
                        unit_cost=float(item_data.get("unit_cost", 0))
                        if item_data.get("unit_cost") is not None
                        else 0.0,
                        received_date=received_date,
                        expiry_date=expiry_date,
                        status=item_data.get("status", "available"),
                    )
                    db.add(batch)
                    batches_created += 1

                summary = f"Inventory confirmed: {batches_created} batches created"
                if skipped_no_product > 0:
                    summary += (
                        f", {skipped_no_product} items skipped (no matching product)"
                    )

            else:
                return f"Error: Unknown processing type: {processing_type}"

            # Update staging record
            staging.status = "confirmed"
            staging.authorized_by_user_id = user_id
            staging.confirmed_at = dt.datetime.now(dt.UTC)
            db.commit()

            return f"Confirmed! {summary}"

        except Exception as e:
            db.rollback()
            logger.exception("confirm_file_processing_failed")
            return f"Error confirming file processing: {str(e)}"

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
    }
