from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.db_schema import get_table_schema
# Note: Session import kept for match_product_aliases which still uses db
from app.ai.openai_client import OpenAIError, chat_completions_create_with_http_info
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.vision_client import process_document_with_vision, VisionDocumentResult
from app.core.config import Settings
from app.db.models.products import Products
from app.db.models.product_aliases import ProductAliases
from app.workers.telemetry import record_llm_call, schedule_openrouter_cost_backfill

logger = logging.getLogger(__name__)


def _format_schema_for_extraction(table_names: list[str], role: str = "owner") -> str:
    """
    Format database schema information for extraction prompts.

    Args:
        table_names: List of table names to include
        role: User role (default: "owner" for full access)

    Returns:
        Formatted string describing tables, columns, types, and constraints
    """
    lines = []
    lines.append("Database schema for target tables:\n")

    for table_name in table_names:
        schema = get_table_schema(table_name, role)
        if not schema:
            continue

        lines.append(f"\n## Table: {table_name}")

        # List columns with details
        lines.append("  Columns:")
        for col in schema.get("columns", []):
            col_name = col["name"]
            col_type = col["type"]
            nullable = "optional" if col["nullable"] else "required"
            primary_key = " (PRIMARY KEY)" if col["primary_key"] else ""
            unique = " (UNIQUE)" if col.get("unique") else ""

            # Extract enum values if present
            enum_info = ""
            if "Enum" in col_type:
                # Try to extract enum values from type string
                # Format is usually: Enum('value1', 'value2', name='enum_name')
                import re

                enum_match = re.search(r"Enum\(([^)]+)\)", col_type)
                if enum_match:
                    enum_values = enum_match.group(1)
                    # Extract quoted values
                    value_matches = re.findall(r"'([^']+)'", enum_values)
                    if value_matches:
                        enum_info = f" (enum values: {', '.join(value_matches)})"

            lines.append(
                f"    - {col_name}: {col_type}, {nullable}{primary_key}{unique}{enum_info}"
            )

        # Show unique constraints
        unique_constraints = schema.get("unique_constraints", [])
        if unique_constraints:
            lines.append("  Unique constraints:")
            for constraint in unique_constraints:
                lines.append(f"    - {', '.join(constraint)}")

        # Show foreign keys
        foreign_keys = schema.get("foreign_keys", [])
        if foreign_keys:
            lines.append("  Foreign keys:")
            for fk in foreign_keys:
                lines.append(f"    - {fk['column']} → {fk['references']}")

    return "\n".join(lines)


def extract_invoice_data(
    file_bytes: bytes,
    mime_type: str,
    settings: Settings,
    filename: str | None = None,
    source_page: int | None = None,
) -> dict[str, Any]:
    """
    Extract structured invoice data from a file using vision model.

    This function does NOT require a database session - schema info is read
    from SQLAlchemy model metadata, not from the database.

    Args:
        file_bytes: File bytes (image or PDF)
        mime_type: MIME type of the file
        settings: Application settings
        filename: Optional filename
        source_page: Optional page number (for multi-page PDFs)

    Returns:
        Dictionary with extracted invoice data:
        {
            "supplier_name": str,
            "invoice_number": str,
            "invoice_date": str (ISO format),
            "due_date": str | None,
            "currency": str,
            "line_items": [
                {
                    "description_raw": str,
                    "quantity": float,
                    "unit": str,
                    "unit_price": float,
                    "line_total": float,
                    "currency": str,
                    "tax_amount": float,
                    "source_page": int | None,
                    "row_index": int | None,
                    "raw_row": str | None,
                }
            ],
            "subtotal": float,
            "tax": float,
            "total": float,
        }
    """
    # Get DB schema for invoice_line_items table
    schema_info = _format_schema_for_extraction(["invoices", "invoice_line_items"])

    prompt = f"""Extract all invoice data from this document and return it as JSON.

{schema_info}

IMPORTANT: Map extracted data to the exact database column names shown above.

Required fields (must not be null):
- supplier_name: The company name of the supplier/vendor (required)
- invoice_number: The invoice number or reference (required)
- invoice_date: Invoice date in ISO format YYYY-MM-DD (required)
- currency: Currency code e.g., USD, EUR, HKD (required)

Supplier contact information (optional but extract if visible):
- contact_name: Name of contact person (e.g., "Teresa Leung", "John Smith")
- contact_email: Email address if visible
- contact_phone: Phone number if visible

- line_items: Array of line items, each with:
  - description_raw: Full description of the item as shown (required, maps to invoice_line_items.description_raw)
  - quantity: Numeric quantity (required, Numeric type)
  - unit: Unit of measure string (required, String type)
  - unit_price: Price per unit (required, Numeric type)
  - line_total: Total for this line (required, Numeric type)
  - currency: Currency code (required, String type)
  - tax_amount: Tax amount for this line, use 0 if none (required, Numeric type)
  - source_page: Page number where this item was found ({source_page if source_page is not None else "null if not applicable"})
  - row_index: Row number/index in the document (null if not applicable)
  - raw_row: Original text/row content before extraction (null if not applicable)

Optional fields (use null if not found):
- due_date: Due date in ISO format YYYY-MM-DD (nullable)

Summary fields:
- subtotal: Subtotal before tax (Numeric)
- tax: Total tax amount (Numeric)
- total: Grand total (Numeric)

CRITICAL RULES:
1. Return null for any field you cannot determine - NEVER summarize or guess
2. Use exact column names from the schema above
3. Include trace fields (source_page, row_index, raw_row) for each line_item
4. All numeric fields must be actual numbers, not strings
5. Return ONLY valid JSON, no other text."""

    try:
        result = process_document_with_vision(
            file_bytes, mime_type, prompt, settings, filename
        )
        # Parse JSON from response
        # The model might return JSON wrapped in markdown code blocks
        extracted_text = result.content.strip()
        if extracted_text.startswith("```"):
            # Remove markdown code blocks
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )

        data = json.loads(extracted_text)

        # Ensure trace fields are set for each line item
        if "line_items" in data:
            for idx, item in enumerate(data["line_items"]):
                if source_page is not None and "source_page" not in item:
                    item["source_page"] = source_page
                if "row_index" not in item:
                    item["row_index"] = idx
                if "raw_row" not in item:
                    item["raw_row"] = None
                # Ensure description_raw is used (backward compatibility)
                if "description" in item and "description_raw" not in item:
                    item["description_raw"] = item["description"]

        # Store telemetry for access by file_processing_tasks
        data["_telemetry_results"] = result.telemetry_results
        return data
    except json.JSONDecodeError as e:
        logger.exception("invoice_extraction_json_parse_failed")
        raise OpenAIError(f"Failed to parse invoice data as JSON: {e}") from e
    except Exception as e:
        logger.exception("invoice_extraction_failed")
        raise OpenAIError(f"Invoice extraction failed: {e}") from e


def extract_price_list_data(
    file_bytes: bytes,
    mime_type: str,
    settings: Settings,
    filename: str | None = None,
    source_page: int | None = None,
) -> dict[str, Any]:
    """
    Extract structured price list data from a file using vision model.

    This function does NOT require a database session - schema info is read
    from SQLAlchemy model metadata, not from the database.

    Args:
        file_bytes: File bytes (image, PDF, CSV, XLSX)
        mime_type: MIME type of the file
        settings: Application settings
        filename: Optional filename
        source_page: Optional page number (for multi-page PDFs)

    Returns:
        Dictionary with extracted price list data:
        {
            "supplier_name": str,
            "effective_date": str | None (ISO format),
            "currency": str,
            "items": [
                {
                    "supplier_name_raw": str,
                    "supplier_sku": str | None,
                    "pack_size_text": str | None,
                    "unit_basis": str | None (enum: "kg", "pack", "piece"),
                    "min_order_qty": float | None,
                    "price": float,
                    "currency": str,
                    "price_type": str (enum: "standard", "promo", "special"),
                    "min_qty": float | None,
                    "valid_from": str (ISO format),
                    "valid_to": str | None (ISO format),
                    "source_page": int | None,
                    "row_index": int | None,
                    "raw_row": str | None,
                }
            ],
        }
    """
    # Get DB schema for supplier_items and supplier_prices tables
    schema_info = _format_schema_for_extraction(["supplier_items", "supplier_prices"])

    prompt = f"""Extract all price list data from this document and return it as JSON.

{schema_info}

IMPORTANT: Map extracted data to the exact database column names shown above.

Required fields (must not be null):
- supplier_name: The company name of the supplier/vendor (required)
- currency: Currency code e.g., USD, EUR, HKD (required)

Supplier contact information (optional but extract if visible):
- contact_name: Name of contact person (e.g., "Teresa Leung", "John Smith")
- contact_email: Email address if visible
- contact_phone: Phone number if visible

- items: Array of items, each with:
  - supplier_name_raw: Product name as shown in the document (required, maps to supplier_items.supplier_name_raw)
  - price: Price per unit (required, Numeric type, maps to supplier_prices.price)
  - currency: Currency code (required, String type, maps to supplier_prices.currency)
  - price_type: Price type (required, enum: "standard", "promo", "special", maps to supplier_prices.price_type)
  - valid_from: Effective/valid from date in ISO format YYYY-MM-DD (required, maps to supplier_prices.valid_from)

Optional fields (use null if not found):
- effective_date: Effective date in ISO format YYYY-MM-DD (nullable)
- supplier_sku: SKU or product code (nullable, maps to supplier_items.supplier_sku)
- pack_size_text: Pack size description e.g., "10 x 1kg" (nullable, maps to supplier_items.pack_size_text)
- unit_basis: Unit basis (nullable, enum: "kg", "pack", "piece", maps to supplier_items.unit_basis)
- min_order_qty: Minimum order quantity (nullable, Numeric, maps to supplier_items.min_order_qty)
- min_qty: Minimum quantity for this price (nullable, Numeric, maps to supplier_prices.min_qty)
- valid_to: Valid until date in ISO format YYYY-MM-DD (nullable, maps to supplier_prices.valid_to)
- source_page: Page number where this item was found ({source_page if source_page is not None else "null if not applicable"})
- row_index: Row number/index in the document (null if not applicable)
- raw_row: Original text/row content before extraction (null if not applicable)

NORMALIZATION RULES:
1. Normalize product names: Extract base name and separate size/pack/MOQ information
2. Split variants: If same base name appears with different sizes/prices, create separate items
3. Example: "Tomatoes 1kg" and "Tomatoes 5kg" should be two items with:
   - supplier_name_raw: "Tomatoes" (base name)
   - pack_size_text: "1kg" and "5kg" respectively
   - unit_basis: "kg" for both

CRITICAL RULES:
1. Return null for any field you cannot determine - NEVER summarize or guess
2. Use exact column names from the schema above
3. Include trace fields (source_page, row_index, raw_row) for each item
4. All numeric fields must be actual numbers, not strings
5. Return ONLY valid JSON, no other text."""

    try:
        result = process_document_with_vision(
            file_bytes, mime_type, prompt, settings, filename
        )
        # Parse JSON from response
        extracted_text = result.content.strip()
        if extracted_text.startswith("```"):
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )

        data = json.loads(extracted_text)

        # Ensure trace fields are set for each item
        if "items" in data:
            for idx, item in enumerate(data["items"]):
                if source_page is not None and "source_page" not in item:
                    item["source_page"] = source_page
                if "row_index" not in item:
                    item["row_index"] = idx
                if "raw_row" not in item:
                    item["raw_row"] = None
                # Ensure supplier_name_raw is used (backward compatibility)
                if "name" in item and "supplier_name_raw" not in item:
                    item["supplier_name_raw"] = item["name"]

        # Store telemetry for access by file_processing_tasks
        data["_telemetry_results"] = result.telemetry_results
        return data
    except json.JSONDecodeError as e:
        logger.exception("price_list_extraction_json_parse_failed")
        raise OpenAIError(f"Failed to parse price list data as JSON: {e}") from e
    except Exception as e:
        logger.exception("price_list_extraction_failed")
        raise OpenAIError(f"Price list extraction failed: {e}") from e


def extract_inventory_data(
    file_bytes: bytes,
    mime_type: str,
    settings: Settings,
    filename: str | None = None,
    source_page: int | None = None,
) -> dict[str, Any]:
    """
    Extract structured inventory data from a photo using vision model.

    This function does NOT require a database session - schema info is read
    from SQLAlchemy model metadata, not from the database.

    Args:
        file_bytes: Image file bytes
        mime_type: MIME type of the file
        settings: Application settings
        filename: Optional filename
        source_page: Optional page number (for multi-page PDFs)

    Returns:
        Dictionary with extracted inventory data:
        {
            "items": [
                {
                    "product_name": str,
                    "quantity": float (required),
                    "unit": str (required),
                    "unit_cost": float | None,
                    "received_date": str | None (ISO format),
                    "expiry_date": str | None (ISO format),
                    "status": str | None (enum: "available", "consumed", "expired"),
                    "location": str | None,
                    "source_page": int | None,
                    "row_index": int | None,
                    "raw_row": str | None,
                }
            ],
        }
    """
    # Get DB schema for inventory_batches table
    schema_info = _format_schema_for_extraction(["inventory_batches"])

    prompt = f"""Analyze this inventory photo and extract all visible items/products.

{schema_info}

IMPORTANT: Map extracted data to the exact database column names shown above.

Required fields (must not be null):
- items: Array of detected items, each with:
  - product_name: Name or description of the product/item (for matching to products table)
  - quantity: Numeric quantity (required, Numeric type, maps to inventory_batches.quantity)
  - unit: Unit of measure (required, String type, maps to inventory_batches.unit)

Optional fields (use null if not found):
- unit_cost: Unit cost/price if visible (nullable, Numeric, maps to inventory_batches.unit_cost)
- received_date: Date received in ISO format YYYY-MM-DD if visible (nullable, maps to inventory_batches.received_date)
- expiry_date: Expiry date in ISO format YYYY-MM-DD if visible (nullable, maps to inventory_batches.expiry_date)
- status: Batch status (nullable, enum: "available", "consumed", "expired", maps to inventory_batches.status)
- location: Location description if visible e.g., "Freezer A", "Shelf 3" (nullable, for inventory_locations lookup)
- source_page: Page number where this item was found ({source_page if source_page is not None else "null if not applicable"})
- row_index: Row number/index in the document (null if not applicable)
- raw_row: Original text/row content before extraction (null if not applicable)

CRITICAL RULES:
1. Return null for any field you cannot determine - NEVER summarize or guess
2. Use exact column names from the schema above
3. Include trace fields (source_page, row_index, raw_row) for each item
4. All numeric fields must be actual numbers, not strings
5. Return ONLY valid JSON, no other text."""

    try:
        result = process_document_with_vision(
            file_bytes, mime_type, prompt, settings, filename
        )
        # Parse JSON from response
        extracted_text = result.content.strip()
        if extracted_text.startswith("```"):
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = (
                "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
            )

        data = json.loads(extracted_text)

        # Ensure trace fields are set for each item
        if "items" in data:
            for idx, item in enumerate(data["items"]):
                if source_page is not None and "source_page" not in item:
                    item["source_page"] = source_page
                if "row_index" not in item:
                    item["row_index"] = idx
                if "raw_row" not in item:
                    item["raw_row"] = None

        # Store telemetry for access by file_processing_tasks
        data["_telemetry_results"] = result.telemetry_results
        return data
    except json.JSONDecodeError as e:
        logger.exception("inventory_extraction_json_parse_failed")
        raise OpenAIError(f"Failed to parse inventory data as JSON: {e}") from e
    except Exception as e:
        logger.exception("inventory_extraction_failed")
        raise OpenAIError(f"Inventory extraction failed: {e}") from e


def match_product_aliases(
    restaurant_id: uuid.UUID,
    supplier_names: list[str],
    db: Session,
    settings: Settings,
    session_id: uuid.UUID | None = None,
    chat_id: int | None = None,
) -> dict[str, Any]:
    """
    Match supplier product names to existing products using LLM.

    Args:
        restaurant_id: Restaurant ID to scope the search
        supplier_names: List of supplier product names to match
        db: Database session
        settings: Application settings

    Returns:
        Dictionary with matches:
        {
            "matches": [
                {
                    "supplier_name": str,
                    "product_id": str | None,
                    "confidence": float (0.0-1.0),
                    "needs_confirmation": bool,
                }
            ],
            "ambiguous": [
                {
                    "supplier_name": str,
                    "candidates": [
                        {"product_id": str, "product_name": str, "confidence": float}
                    ],
                }
            ],
        }
    """
    # Load existing products and aliases for this restaurant
    products = db.scalars(
        select(Products).where(
            Products.restaurant_id == restaurant_id, Products.is_active == True
        )
    ).all()

    aliases = db.scalars(
        select(ProductAliases)
        .join(Products)
        .where(Products.restaurant_id == restaurant_id)
    ).all()

    if not products:
        # No products to match against
        return {
            "matches": [
                {
                    "supplier_name": name,
                    "product_id": None,
                    "confidence": 0.0,
                    "needs_confirmation": True,
                }
                for name in supplier_names
            ],
            "ambiguous": [],
        }

    # Build context for LLM
    products_list = [
        {
            "id": str(p.id),
            "name_en": p.name_en,
            "name_local": p.name_local,
            "category": p.category,
        }
        for p in products
    ]

    aliases_list = [
        {
            "product_id": str(a.product_id),
            "alias_text": a.alias_text,
            "confidence": a.confidence,
        }
        for a in aliases
    ]

    prompt = f"""Match the following supplier product names to existing products.

Existing products:
{json.dumps(products_list, indent=2)}

Existing product aliases:
{json.dumps(aliases_list, indent=2)}

Supplier product names to match:
{json.dumps(supplier_names, indent=2)}

For each supplier name, determine:
1. The best matching product_id (if any)
2. A confidence score (0.0 to 1.0) indicating how certain the match is
3. Whether user confirmation is needed (needs_confirmation: true if confidence < 0.8)

Return JSON in this format:
{{
    "matches": [
        {{
            "supplier_name": str,
            "product_id": str | null,
            "confidence": float,
            "needs_confirmation": bool
        }}
    ],
    "ambiguous": [
        {{
            "supplier_name": str,
            "candidates": [
                {{"product_id": str, "product_name": str, "confidence": float}}
            ]
        }}
    ]
}}

Return ONLY valid JSON, no other text."""

    try:
        messages = [{"role": "user", "content": prompt}]
        start_time = time.time()
        response, headers, latency_ms = chat_completions_create_with_http_info(
            settings=settings,
            messages=messages,
            temperature=0.2,
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise OpenAIError(f"Unexpected LLM response: {response}")

        # Extract telemetry data
        generation_id = extract_openrouter_generation_id(headers)
        usage = extract_openrouter_usage(response)
        model = settings.openai_model

        # Record telemetry if session_id is available
        if session_id:
            try:
                llm_call_id = record_llm_call(
                    db=db,
                    session_id=session_id,
                    chat_id=chat_id,
                    purpose="product_alias_matching",
                    model=model,
                    openrouter_generation_id=generation_id,
                    upstream_id=response.get("id")
                    if isinstance(response.get("id"), str)
                    else None,
                    provider_name=response.get("provider")
                    if isinstance(response.get("provider"), str)
                    else None,
                    usage=usage,
                    latency_ms=int(latency_ms),
                    error=None,
                )
                db.commit()

                # Schedule cost backfill if generation ID exists
                if generation_id:
                    try:
                        schedule_openrouter_cost_backfill(
                            llm_call_id=llm_call_id, delay_seconds=120
                        )
                    except Exception:
                        logger.exception(
                            "product_alias_matching_cost_backfill_schedule_failed"
                        )
            except Exception:
                logger.exception("product_alias_matching_telemetry_failed")
                # Don't fail the function if telemetry fails

        # Parse JSON
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
        if content.startswith("```json"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        data = json.loads(content)
        return data
    except json.JSONDecodeError as e:
        logger.exception("product_alias_matching_json_parse_failed")
        raise OpenAIError(f"Failed to parse product alias matches as JSON: {e}") from e
    except Exception as e:
        logger.exception("product_alias_matching_failed")
        raise OpenAIError(f"Product alias matching failed: {e}") from e
