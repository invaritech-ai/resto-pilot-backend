from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.documents import Documents
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.inventory_batches import InventoryBatches
from app.db.models.invoice_line_items import InvoiceLineItems
from app.db.models.invoices import Invoices
from app.db.models.products import Products
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.supplier_item_products import SupplierItemProducts
from app.db.models.supplier_items import SupplierItems
from app.db.models.supplier_prices import SupplierPrices
from app.db.models.suppliers import Suppliers, normalize_supplier_name


class FileProcessingConfirmError(Exception):
    pass


def _parse_date(date_str: str | None) -> dt.datetime | None:
    if not date_str:
        return None
    try:
        date_str_clean = date_str.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(date_str_clean)
    except (ValueError, AttributeError):
        pass

    try:
        if len(date_str) >= 10:
            date_part = date_str[:10]
            return dt.datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    except ValueError:
        return None
    return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _ensure_document_for_staging(*, db: Session, staging: FileProcessingStaging) -> Documents | None:
    if staging.document_id:
        return db.get(Documents, staging.document_id)

    if staging.processing_type not in {"invoice", "price_list"}:
        return None

    if staging.restaurant_id is None:
        return None

    run: FileProcessingRuns | None = None
    if staging.run_id:
        run = db.get(FileProcessingRuns, staging.run_id)

    file_url = run.file_id if run else f"staging_{staging.id}"
    doc = Documents(
        restaurant_id=staging.restaurant_id,
        supplier_id=staging.supplier_id,
        doc_type=staging.processing_type,
        file_url=file_url,
        uploaded_at=dt.datetime.now(dt.UTC),
    )
    db.add(doc)
    db.flush()

    staging.document_id = doc.id
    if run and not run.document_id:
        run.document_id = doc.id

    return doc


def _ensure_restaurant_supplier_link(
    *,
    db: Session,
    restaurant_id: uuid.UUID | None,
    supplier_id: uuid.UUID,
    default_currency: str | None,
) -> None:
    if restaurant_id is None:
        return

    link = db.scalar(
        select(RestaurantSuppliers).where(
            RestaurantSuppliers.restaurant_id == restaurant_id,
            RestaurantSuppliers.supplier_id == supplier_id,
        )
    )
    if not link:
        link = RestaurantSuppliers(
            restaurant_id=restaurant_id,
            supplier_id=supplier_id,
            status="active",
            default_currency=default_currency,
        )
        db.add(link)
        return

    if default_currency and not link.default_currency:
        link.default_currency = default_currency


def _upsert_supplier(
    *,
    db: Session,
    supplier_name: str,
    owner_user_id: uuid.UUID,
    contact_name: str | None,
    contact_email: str | None,
    contact_phone: str | None,
    currency: str | None,
    preferred_supplier_id: uuid.UUID | None,
) -> Suppliers:
    supplier: Suppliers | None = None
    if preferred_supplier_id:
        supplier = db.get(Suppliers, preferred_supplier_id)
        if supplier and supplier.user_id is not None and supplier.user_id != owner_user_id:
            raise FileProcessingConfirmError("Supplier does not belong to this user.")

    if not supplier:
        normalized = normalize_supplier_name(supplier_name)
        supplier = db.scalar(
            select(Suppliers).where(
                Suppliers.name_normalized == normalized,
                Suppliers.is_active,
                (Suppliers.user_id == owner_user_id) | (Suppliers.user_id.is_(None)),
            )
        )

    if not supplier:
        supplier = Suppliers(
            user_id=owner_user_id,
            name=supplier_name,
            name_normalized=normalize_supplier_name(supplier_name),
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            currency=currency,
            is_active=True,
        )
        db.add(supplier)
        db.flush()
        return supplier

    if supplier.user_id is None:
        supplier.user_id = owner_user_id

    supplier.name = supplier_name
    supplier.name_normalized = supplier.name_normalized or normalize_supplier_name(supplier_name)
    if contact_name:
        supplier.contact_name = contact_name
    if contact_email:
        supplier.contact_email = contact_email
    if contact_phone:
        supplier.contact_phone = contact_phone
    if currency:
        supplier.currency = currency

    return supplier


def _find_supplier_item(
    *,
    db: Session,
    supplier_id: uuid.UUID,
    supplier_sku: str | None,
    supplier_name_raw: str,
) -> SupplierItems | None:
    if supplier_sku:
        item = db.scalar(
            select(SupplierItems).where(
                SupplierItems.supplier_id == supplier_id,
                SupplierItems.supplier_sku == supplier_sku,
                SupplierItems.status == "active",
            )
        )
        if item:
            return item

    return db.scalar(
        select(SupplierItems).where(
            SupplierItems.supplier_id == supplier_id,
            SupplierItems.supplier_name_raw.ilike(supplier_name_raw),
            SupplierItems.status == "active",
        )
    )


def _upsert_supplier_item(
    *,
    db: Session,
    supplier_id: uuid.UUID,
    item_data: dict[str, Any],
    source_document_id: uuid.UUID | None,
) -> tuple[SupplierItems, bool]:
    supplier_name_raw = _clean_str(item_data.get("supplier_name_raw") or item_data.get("name")) or ""
    if not supplier_name_raw:
        raise FileProcessingConfirmError("Item supplier_name_raw is required.")

    supplier_sku = _clean_str(item_data.get("supplier_sku"))
    pack_size_text = _clean_str(item_data.get("pack_size_text"))
    unit_basis = _clean_str(item_data.get("unit_basis"))
    if unit_basis:
        unit_basis = unit_basis.lower()
    if unit_basis not in {None, "kg", "pack", "piece"}:
        unit_basis = None

    min_order_qty = _parse_float(item_data.get("min_order_qty"))

    existing_item = _find_supplier_item(
        db=db,
        supplier_id=supplier_id,
        supplier_sku=supplier_sku,
        supplier_name_raw=supplier_name_raw,
    )

    if not existing_item:
        supplier_item = SupplierItems(
            supplier_id=supplier_id,
            product_id=None,
            supplier_sku=supplier_sku,
            supplier_name_raw=supplier_name_raw,
            pack_size_text=pack_size_text,
            unit_basis=unit_basis,
            min_order_qty=min_order_qty,
            status="active",
            source_document_id=source_document_id,
        )
        db.add(supplier_item)
        db.flush()
        return supplier_item, True

    existing_item.status = "active"
    existing_item.supplier_name_raw = supplier_name_raw
    if supplier_sku:
        existing_item.supplier_sku = supplier_sku
    if pack_size_text:
        existing_item.pack_size_text = pack_size_text
    if unit_basis:
        existing_item.unit_basis = unit_basis
    if min_order_qty is not None:
        existing_item.min_order_qty = min_order_qty
    if source_document_id:
        existing_item.source_document_id = source_document_id
    return existing_item, False


def _upsert_supplier_item_product_link(
    *,
    db: Session,
    supplier_item_id: uuid.UUID,
    restaurant_id: uuid.UUID | None,
    product_id_value: Any,
) -> None:
    if restaurant_id is None:
        return
    if not product_id_value:
        return
    try:
        product_uuid = uuid.UUID(str(product_id_value))
    except ValueError:
        return

    link = db.scalar(
        select(SupplierItemProducts).where(
            SupplierItemProducts.supplier_item_id == supplier_item_id,
            SupplierItemProducts.restaurant_id == restaurant_id,
        )
    )
    if link:
        link.product_id = product_uuid
        return

    db.add(
        SupplierItemProducts(
            supplier_item_id=supplier_item_id,
            restaurant_id=restaurant_id,
            product_id=product_uuid,
        )
    )


def confirm_file_processing_staging(
    *,
    db: Session,
    staging: FileProcessingStaging,
    owner_user_id: uuid.UUID,
    authorized_by_user_id: uuid.UUID,
) -> dict[str, Any]:
    if staging.status != "pending_review":
        raise FileProcessingConfirmError(
            f"This record is already {staging.status}. Cannot confirm."
        )

    extracted_data = staging.extracted_data_json
    if not isinstance(extracted_data, dict):
        extracted_data = {}

    processing_type = staging.processing_type
    now = dt.datetime.now(dt.UTC)

    try:
        if processing_type == "invoice":
            if staging.restaurant_id is None:
                raise FileProcessingConfirmError("restaurant_id is required for invoice processing.")

            document = _ensure_document_for_staging(db=db, staging=staging)
            if not document:
                raise FileProcessingConfirmError("Document record not found.")

            supplier_name = _clean_str(extracted_data.get("supplier_name"))
            if not supplier_name and staging.supplier_id:
                existing_supplier = db.get(Suppliers, staging.supplier_id)
                if existing_supplier:
                    supplier_name = existing_supplier.name
            if not supplier_name:
                raise FileProcessingConfirmError("Supplier name is required for invoice processing.")

            currency = _clean_str(extracted_data.get("currency")) or "USD"
            supplier = _upsert_supplier(
                db=db,
                supplier_name=supplier_name,
                owner_user_id=owner_user_id,
                contact_name=None,
                contact_email=None,
                contact_phone=None,
                currency=currency,
                preferred_supplier_id=staging.supplier_id,
            )
            staging.supplier_id = supplier.id
            document.supplier_id = supplier.id

            _ensure_restaurant_supplier_link(
                db=db,
                restaurant_id=staging.restaurant_id,
                supplier_id=supplier.id,
                default_currency=currency,
            )

            invoice_date = _parse_date(_clean_str(extracted_data.get("invoice_date"))) or now
            due_date = _parse_date(_clean_str(extracted_data.get("due_date")))

            invoice = Invoices(
                restaurant_id=staging.restaurant_id,
                supplier_id=supplier.id,
                invoice_number=_clean_str(extracted_data.get("invoice_number")) or "",
                invoice_date=invoice_date,
                due_date=due_date,
                currency=currency,
                subtotal=_parse_float(extracted_data.get("subtotal")) or 0.0,
                tax=_parse_float(extracted_data.get("tax")) or 0.0,
                total=_parse_float(extracted_data.get("total")) or 0.0,
                document_id=document.id,
                status="received",
                authorized_by_user_id=authorized_by_user_id,
            )
            db.add(invoice)
            db.flush()

            line_items_created = 0
            for item in extracted_data.get("line_items", []) or []:
                if not isinstance(item, dict):
                    continue
                db.add(
                    InvoiceLineItems(
                        invoice_id=invoice.id,
                        supplier_id=supplier.id,
                        description_raw=_clean_str(item.get("description_raw") or item.get("description"))
                        or "",
                        quantity=_parse_float(item.get("quantity")) or 0.0,
                        unit=_clean_str(item.get("unit")) or "",
                        unit_price=_parse_float(item.get("unit_price")) or 0.0,
                        line_total=_parse_float(item.get("line_total")) or 0.0,
                        currency=currency,
                        tax_amount=_parse_float(item.get("tax_amount")) or 0.0,
                    )
                )
                line_items_created += 1

            summary = {
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "line_items_created": line_items_created,
                "total": float(invoice.total),
                "currency": invoice.currency,
            }

        elif processing_type == "price_list":
            document = _ensure_document_for_staging(db=db, staging=staging)

            supplier_name = _clean_str(extracted_data.get("supplier_name"))
            if not supplier_name and staging.supplier_id:
                existing_supplier = db.get(Suppliers, staging.supplier_id)
                if existing_supplier:
                    supplier_name = existing_supplier.name
            if not supplier_name:
                raise FileProcessingConfirmError("Supplier name is required for price list processing.")

            currency = _clean_str(extracted_data.get("currency")) or "USD"
            supplier = _upsert_supplier(
                db=db,
                supplier_name=supplier_name,
                owner_user_id=owner_user_id,
                contact_name=_clean_str(extracted_data.get("contact_name")),
                contact_email=_clean_str(extracted_data.get("contact_email")),
                contact_phone=_clean_str(extracted_data.get("contact_phone")),
                currency=currency,
                preferred_supplier_id=staging.supplier_id,
            )
            staging.supplier_id = supplier.id
            if document:
                document.supplier_id = supplier.id

            _ensure_restaurant_supplier_link(
                db=db,
                restaurant_id=staging.restaurant_id,
                supplier_id=supplier.id,
                default_currency=currency,
            )

            effective_date = _parse_date(_clean_str(extracted_data.get("effective_date"))) or now

            items_created = 0
            items_updated = 0
            prices_created = 0

            for item_data in extracted_data.get("items", []) or []:
                if not isinstance(item_data, dict):
                    continue

                supplier_item, created = _upsert_supplier_item(
                    db=db,
                    supplier_id=supplier.id,
                    item_data=item_data,
                    source_document_id=document.id if document else None,
                )
                if created:
                    items_created += 1
                else:
                    items_updated += 1

                _upsert_supplier_item_product_link(
                    db=db,
                    supplier_item_id=supplier_item.id,
                    restaurant_id=staging.restaurant_id,
                    product_id_value=item_data.get("product_id"),
                )

                valid_from = _parse_date(_clean_str(item_data.get("valid_from"))) or effective_date
                valid_to = _parse_date(_clean_str(item_data.get("valid_to")))

                price_value = _parse_float(item_data.get("price"))
                if price_value is None:
                    continue

                price_type = _clean_str(item_data.get("price_type")) or "standard"
                if price_type not in {"standard", "promo", "special"}:
                    price_type = "standard"

                db.add(
                    SupplierPrices(
                        supplier_item_id=supplier_item.id,
                        price=price_value,
                        currency=_clean_str(item_data.get("currency")) or currency,
                        price_type=price_type,
                        valid_from=valid_from,
                        valid_to=valid_to,
                        min_qty=_parse_float(item_data.get("min_qty")),
                        source_document_id=document.id if document else None,
                    )
                )
                prices_created += 1

            summary = {
                "supplier_id": str(supplier.id),
                "document_id": str(document.id) if document else None,
                "items_created": items_created,
                "items_updated": items_updated,
                "prices_created": prices_created,
                "currency": currency,
            }

        elif processing_type == "inventory":
            if staging.restaurant_id is None:
                raise FileProcessingConfirmError("restaurant_id is required for inventory processing.")

            batches_created = 0
            skipped_no_product = 0

            for item_data in extracted_data.get("items", []) or []:
                if not isinstance(item_data, dict):
                    continue
                product_name = _clean_str(item_data.get("product_name"))
                if not product_name:
                    continue

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
                    continue

                supplier_id: uuid.UUID | None = None
                supplier_id_value = item_data.get("supplier_id")
                if supplier_id_value:
                    try:
                        supplier_id = uuid.UUID(str(supplier_id_value))
                    except ValueError:
                        supplier_id = None
                if not supplier_id and staging.supplier_id:
                    supplier_id = staging.supplier_id

                if not supplier_id:
                    supplier_name = _clean_str(extracted_data.get("supplier_name"))
                    if supplier_name:
                        supplier = _upsert_supplier(
                            db=db,
                            supplier_name=supplier_name,
                            owner_user_id=owner_user_id,
                            contact_name=None,
                            contact_email=None,
                            contact_phone=None,
                            currency=None,
                            preferred_supplier_id=None,
                        )
                        _ensure_restaurant_supplier_link(
                            db=db,
                            restaurant_id=staging.restaurant_id,
                            supplier_id=supplier.id,
                            default_currency=None,
                        )
                        supplier_id = supplier.id

                if not supplier_id:
                    skipped_no_product += 1
                    continue

                received_date = _parse_date(_clean_str(item_data.get("received_date"))) or now
                expiry_date = _parse_date(_clean_str(item_data.get("expiry_date")))

                db.add(
                    InventoryBatches(
                        restaurant_id=staging.restaurant_id,
                        product_id=product.id,
                        supplier_id=supplier_id,
                        quantity=_parse_float(item_data.get("quantity")) or 0.0,
                        unit=_clean_str(item_data.get("unit")) or "",
                        unit_cost=_parse_float(item_data.get("unit_cost")) or 0.0,
                        received_date=received_date,
                        expiry_date=expiry_date,
                        status=_clean_str(item_data.get("status")) or "available",
                    )
                )
                batches_created += 1

            summary = {
                "batches_created": batches_created,
                "skipped_no_product": skipped_no_product,
            }

        else:
            raise FileProcessingConfirmError(f"Unknown processing type: {processing_type}")

        staging.status = "confirmed"
        staging.authorized_by_user_id = authorized_by_user_id
        staging.confirmed_at = now

        db.commit()
        return {
            "processing_type": processing_type,
            "staging_id": str(staging.id),
            "summary": summary,
        }
    except Exception:
        db.rollback()
        raise
