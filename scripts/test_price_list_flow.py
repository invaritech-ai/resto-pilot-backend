#!/usr/bin/env python3
"""
Simulate the full price list flow without Telegram API.

Usage:
  uv run python scripts/test_price_list_flow.py /path/to/file.pdf \
    [--restaurant-id <uuid>] [--user-id <uuid>] [--chat-id 0] \
    [--supplier-id <uuid>] [--dry-run] [--no-db]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.processing.file_processor import extract_price_list_data
from app.db.models.documents import Documents
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.suppliers import Suppliers
from app.db.models.user import User
from app.db.models.restaurant import Restaurant
from app.workers.file_processing_tasks import (
    _set_pending_file_processing_action,
    _set_pending_file_processing_confirm_action,
    _clear_pending_file_processing_action,
)
from app.ai.vision_client import _clean_structured_text


def _determine_mime_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".csv"):
        return "text/csv"
    if name.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _get_db_session(use_real_db: bool) -> Session:
    settings = get_settings()
    db_url = settings.database_url if use_real_db else "sqlite:///:memory:"
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


def _build_preview_message(extracted_data: dict) -> str:
    supplier_name = (extracted_data.get("supplier_name") or "").strip()
    currency = (extracted_data.get("currency") or "").strip()
    items = extracted_data.get("items", []) or []
    total_items = len(items)

    lines = ["Price List Extracted\n"]
    lines.append(f"Supplier: {supplier_name or '[missing]'}")
    contact_name = extracted_data.get("contact_name")
    contact_email = extracted_data.get("contact_email")
    contact_phone = extracted_data.get("contact_phone")
    if contact_name:
        lines.append(f"Contact: {contact_name}")
    if contact_email:
        lines.append(f"Email: {contact_email}")
    if contact_phone:
        lines.append(f"Phone: {contact_phone}")
    lines.append(f"Currency: {currency or '[missing]'}")
    lines.append(f"Total Items: {total_items}")

    effective_date = extracted_data.get("effective_date")
    if effective_date:
        lines.append(f"Effective Date: {effective_date}")

    sample_size = min(5, total_items)
    if sample_size:
        lines.append(f"\nSample Items (first {sample_size} of {total_items}):")
        for i, item in enumerate(items[:sample_size], 1):
            name = item.get("supplier_name_raw", item.get("name", "N/A"))
            price = item.get("price", "N/A")
            pack_size = item.get("pack_size_text", "")
            item_line = f"  {i}. {name}"
            if pack_size:
                item_line += f" ({pack_size})"
            item_line += f" - {currency} {price}"
            lines.append(item_line)

    lines.append("\n--")
    lines.append("Say /confirm to save all items to your database")
    lines.append("Ask to show specific items or categories")
    lines.append("Tell me if anything needs correcting")
    return "\n".join(lines)


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid {label}: {value}") from exc


def _to_datetime_string(value: str | None) -> str | None:
    if not value:
        return None
    if "T" in value:
        return value
    return f"{value}T00:00:00+00:00"


def _build_insertables(
    supplier: dict,
    product_info: list[dict],
    restaurant_id: str | None,
    supplier_id: str | None,
    document_id: str | None,
) -> dict[str, list[dict] | dict]:
    supplier_row = {
        "restaurant_id": restaurant_id or "<RESTAURANT_ID>",
        "name": supplier.get("name"),
        "contact_name": supplier.get("contact_name"),
        "contact_email": supplier.get("contact_email"),
        "contact_phone": supplier.get("contact_phone"),
        "language": supplier.get("language"),
        "currency": supplier.get("currency"),
        "lead_time_days": supplier.get("lead_time_days"),
        "notes": None,
        "is_active": True,
    }

    product_rows = []
    supplier_item_rows = []
    supplier_price_rows = []

    for entry in product_info:
        product_rows.append(
            {
                "restaurant_id": restaurant_id or "<RESTAURANT_ID>",
                "name_en": entry.get("name_en"),
                "name_local": entry.get("name_local"),
                "category": entry.get("category"),
                "sub_category": entry.get("sub_category"),
                "storage_type": entry.get("storage_type"),
                "default_unit": entry.get("default_unit"),
                "default_unit_size": entry.get("default_unit_size"),
                "is_active": True,
            }
        )
        supplier_item_rows.append(
            {
                "supplier_id": supplier_id or "<SUPPLIER_ID>",
                "product_id": "<PRODUCT_ID>",
                "supplier_sku": entry.get("supplier_sku"),
                "supplier_name_raw": entry.get("supplier_name_raw"),
                "pack_size_text": entry.get("pack_size_text"),
                "unit_basis": entry.get("unit_basis"),
                "min_order_qty": entry.get("min_order_qty"),
                "status": "active",
                "source_document_id": document_id or "<DOCUMENT_ID>",
            }
        )
        supplier_price_rows.append(
            {
                "supplier_item_id": "<SUPPLIER_ITEM_ID>",
                "price": entry.get("price"),
                "currency": entry.get("currency") or supplier.get("currency"),
                "price_type": entry.get("price_type"),
                "valid_from": _to_datetime_string(entry.get("valid_from")),
                "valid_to": _to_datetime_string(entry.get("valid_to")),
                "min_qty": entry.get("min_qty"),
                "source_document_id": document_id or "<DOCUMENT_ID>",
            }
        )

    return {
        "suppliers": supplier_row,
        "products": product_rows,
        "supplier_items": supplier_item_rows,
        "supplier_prices": supplier_price_rows,
    }


def simulate_price_list_flow(args: argparse.Namespace) -> None:
    file_path = Path(args.file_path)
    if not file_path.exists():
        raise SystemExit(f"File not found: {file_path}")

    settings = get_settings()
    configure_logging(settings)

    file_bytes = file_path.read_bytes()
    filename = file_path.name
    mime_type = _determine_mime_type(file_path)

    print("[FLOW] Starting price list flow simulation")
    print(f"[FLOW] File: {file_path}")
    print(f"[FLOW] MIME type: {mime_type}")
    print(f"[FLOW] Bytes: {len(file_bytes):,}")

    restaurant_uuid = (
        _parse_uuid(args.restaurant_id, "restaurant-id")
        if args.restaurant_id
        else None
    )
    user_uuid = _parse_uuid(args.user_id, "user-id") if args.user_id else None
    supplier_uuid = (
        _parse_uuid(args.supplier_id, "supplier-id") if args.supplier_id else None
    )

    use_real_db = not args.no_db
    db = _get_db_session(use_real_db)
    try:
        if not args.dry_run:
            if not restaurant_uuid or not user_uuid:
                raise SystemExit("restaurant-id and user-id are required without --dry-run")
            if not db.get(User, user_uuid):
                raise SystemExit(f"User not found: {user_uuid}")
            if not db.get(Restaurant, restaurant_uuid):
                raise SystemExit(f"Restaurant not found: {restaurant_uuid}")

        print("[FLOW] Step 1: extract_price_list_data")
        extracted_data = extract_price_list_data(
            file_bytes=file_bytes,
            mime_type=mime_type,
            settings=settings,
            db=db,
            filename=filename,
        )

        telemetry_results = extracted_data.pop("_telemetry_results", [])
        print(f"[FLOW] Telemetry calls: {len(telemetry_results)}")

        ocr_results = []
        structured_results = []
        for result in telemetry_results:
            cleaned = _clean_structured_text(result.content or "")
            if cleaned.startswith("{"):
                try:
                    structured_results.append(json.loads(cleaned))
                    continue
                except Exception:
                    pass
            ocr_results.append(result)

        if structured_results:
            print("\n[FLOW] Structured extraction per page:")
            for idx, structured in enumerate(structured_results, 1):
                page_number = None
                if structured.get("product_info"):
                    first_item = structured["product_info"][0]
                    if isinstance(first_item, dict):
                        page_number = first_item.get("source_page")
                ocr_text = ""
                if idx - 1 < len(ocr_results):
                    ocr_text = ocr_results[idx - 1].content or ""
                print(f"\n--- Page {page_number or idx} ---")
                print("[MARKDOWN]")
                print(ocr_text.strip() or "[EMPTY]")
                print("\n[JSON]")
                print(json.dumps(structured, indent=2, ensure_ascii=False))

                insertables = _build_insertables(
                    supplier=structured.get("supplier") or {},
                    product_info=structured.get("product_info") or [],
                    restaurant_id=str(restaurant_uuid) if restaurant_uuid else None,
                    supplier_id=str(supplier_uuid) if supplier_uuid else None,
                    document_id=None,
                )
                print("\n[INSERTABLE]")
                print(json.dumps(insertables, indent=2, ensure_ascii=False))

        supplier_name = (extracted_data.get("supplier_name") or "").strip()
        currency = (extracted_data.get("currency") or "").strip()

        if not supplier_uuid and supplier_name and restaurant_uuid:
            supplier = db.scalar(
                select(Suppliers).where(
                    Suppliers.restaurant_id == restaurant_uuid,
                    Suppliers.name.ilike(supplier_name),
                    Suppliers.is_active == True,
                )
            )
            if supplier:
                supplier_uuid = supplier.id

        status = "pending_review"
        if not supplier_name:
            status = "awaiting_supplier"
        elif not currency:
            status = "awaiting_currency"

        if args.dry_run:
            print(f"[FLOW] Dry run: status={status}")
            print(_build_preview_message(extracted_data))
            return

        print("[FLOW] Step 2: create document + staging")
        document = Documents(
            restaurant_id=restaurant_uuid,
            supplier_id=supplier_uuid,
            doc_type="price_list",
            file_url=str(file_path),
            uploaded_at=dt.datetime.now(dt.UTC),
        )
        db.add(document)
        db.flush()

        staging = FileProcessingStaging(
            restaurant_id=restaurant_uuid,
            user_id=user_uuid,
            document_id=document.id,
            processing_type="price_list",
            extracted_data_json=extracted_data,
            product_alias_matches_json={},
            status=status,
        )
        db.add(staging)
        db.commit()
        print(f"[FLOW] Staging created: {staging.id}")

        print("[FLOW] Step 3: set pending action")
        if status == "awaiting_supplier":
            _set_pending_file_processing_action(
                db=db,
                user_id=user_uuid,
                restaurant_id=restaurant_uuid,
                staging_id=staging.id,
                field="supplier",
                supplier_id=supplier_uuid,
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
        else:
            _clear_pending_file_processing_action(
                db=db,
                user_id=user_uuid,
                restaurant_id=restaurant_uuid,
                supplier_id=supplier_uuid,
            )
            _set_pending_file_processing_confirm_action(
                db=db,
                user_id=user_uuid,
                restaurant_id=restaurant_uuid,
                staging_id=staging.id,
                supplier_id=supplier_uuid,
            )
        db.commit()

        print("[FLOW] Step 4: preview message")
        print(_build_preview_message(extracted_data))

    except Exception as exc:
        print(f"[FLOW] ERROR: {exc}")
        traceback.print_exc()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate price list flow without Telegram API")
    parser.add_argument("file_path", help="Path to file (PDF, image, etc.)")
    parser.add_argument("--restaurant-id", help="Restaurant UUID")
    parser.add_argument("--user-id", help="User UUID")
    parser.add_argument("--chat-id", type=int, default=0, help="Chat ID for telemetry")
    parser.add_argument("--supplier-id", help="Supplier UUID (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    parser.add_argument("--no-db", action="store_true", help="Use an in-memory DB")
    args = parser.parse_args()

    simulate_price_list_flow(args)


if __name__ == "__main__":
    main()
