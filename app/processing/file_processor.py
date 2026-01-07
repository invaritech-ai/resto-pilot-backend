from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.openai_client import OpenAIError, chat_completions_create
from app.ai.vision_client import process_document_with_vision
from app.core.config import Settings
from app.db.models.products import Products
from app.db.models.product_aliases import ProductAliases

logger = logging.getLogger(__name__)


def extract_invoice_data(
    file_bytes: bytes, mime_type: str, settings: Settings
) -> dict[str, Any]:
    """
    Extract structured invoice data from a file using vision model.

    Args:
        file_bytes: File bytes (image or PDF)
        mime_type: MIME type of the file
        settings: Application settings

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
                    "description": str,
                    "quantity": float,
                    "unit": str,
                    "unit_price": float,
                    "line_total": float,
                    "tax_amount": float,
                }
            ],
            "subtotal": float,
            "tax": float,
            "total": float,
        }
    """
    prompt = """Extract all invoice data from this document and return it as JSON.

Include:
- supplier_name: The name of the supplier/vendor
- invoice_number: The invoice number or reference
- invoice_date: Invoice date in ISO format (YYYY-MM-DD)
- due_date: Due date in ISO format if present, otherwise null
- currency: Currency code (e.g., USD, EUR)
- line_items: Array of line items, each with:
  - description: Full description of the item
  - quantity: Numeric quantity
  - unit: Unit of measure (kg, pack, piece, etc.)
  - unit_price: Price per unit
  - line_total: Total for this line
  - tax_amount: Tax amount for this line (0 if none)
- subtotal: Subtotal before tax
- tax: Total tax amount
- total: Grand total

Return ONLY valid JSON, no other text."""

    try:
        extracted_text = process_document_with_vision(file_bytes, mime_type, prompt, settings)
        # Parse JSON from response
        # The model might return JSON wrapped in markdown code blocks
        extracted_text = extracted_text.strip()
        if extracted_text.startswith("```"):
            # Remove markdown code blocks
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text

        data = json.loads(extracted_text)
        return data
    except json.JSONDecodeError as e:
        logger.exception("invoice_extraction_json_parse_failed")
        raise OpenAIError(f"Failed to parse invoice data as JSON: {e}") from e
    except Exception as e:
        logger.exception("invoice_extraction_failed")
        raise OpenAIError(f"Invoice extraction failed: {e}") from e


def extract_price_list_data(
    file_bytes: bytes, mime_type: str, settings: Settings
) -> dict[str, Any]:
    """
    Extract structured price list data from a file using vision model.

    Args:
        file_bytes: File bytes (image, PDF, CSV, XLSX)
        mime_type: MIME type of the file
        settings: Application settings

    Returns:
        Dictionary with extracted price list data:
        {
            "supplier_name": str,
            "effective_date": str | None (ISO format),
            "currency": str,
            "items": [
                {
                    "name": str,
                    "sku": str | None,
                    "price": float,
                    "unit": str,
                    "pack_size": str | None,
                    "min_order_qty": float | None,
                }
            ],
        }
    """
    prompt = """Extract all price list data from this document and return it as JSON.

Include:
- supplier_name: The name of the supplier/vendor
- effective_date: Effective date in ISO format (YYYY-MM-DD) if present, otherwise null
- currency: Currency code (e.g., USD, EUR)
- items: Array of items, each with:
  - name: Product name as shown in the document
  - sku: SKU or product code if present, otherwise null
  - price: Price per unit
  - unit: Unit of measure (kg, pack, piece, etc.)
  - pack_size: Pack size description if present (e.g., "10 x 1kg"), otherwise null
  - min_order_qty: Minimum order quantity if specified, otherwise null

Return ONLY valid JSON, no other text."""

    try:
        extracted_text = process_document_with_vision(file_bytes, mime_type, prompt, settings)
        # Parse JSON from response
        extracted_text = extracted_text.strip()
        if extracted_text.startswith("```"):
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text

        data = json.loads(extracted_text)
        return data
    except json.JSONDecodeError as e:
        logger.exception("price_list_extraction_json_parse_failed")
        raise OpenAIError(f"Failed to parse price list data as JSON: {e}") from e
    except Exception as e:
        logger.exception("price_list_extraction_failed")
        raise OpenAIError(f"Price list extraction failed: {e}") from e


def extract_inventory_data(
    file_bytes: bytes, mime_type: str, settings: Settings
) -> dict[str, Any]:
    """
    Extract structured inventory data from a photo using vision model.

    Args:
        file_bytes: Image file bytes
        mime_type: MIME type of the file
        settings: Application settings

    Returns:
        Dictionary with extracted inventory data:
        {
            "items": [
                {
                    "product_name": str,
                    "quantity": float | None,
                    "unit": str | None,
                    "location": str | None,
                    "notes": str | None,
                }
            ],
        }
    """
    prompt = """Analyze this inventory photo and extract all visible items/products.

Return the data as JSON with:
- items: Array of detected items, each with:
  - product_name: Name or description of the product/item
  - quantity: Numeric quantity if visible, otherwise null
  - unit: Unit of measure if visible (kg, pack, piece, etc.), otherwise null
  - location: Location description if visible (e.g., "Freezer A", "Shelf 3"), otherwise null
  - notes: Any additional notes or observations, otherwise null

Return ONLY valid JSON, no other text."""

    try:
        extracted_text = process_document_with_vision(file_bytes, mime_type, prompt, settings)
        # Parse JSON from response
        extracted_text = extracted_text.strip()
        if extracted_text.startswith("```"):
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text
        if extracted_text.startswith("```json"):
            lines = extracted_text.split("\n")
            extracted_text = "\n".join(lines[1:-1]) if len(lines) > 2 else extracted_text

        data = json.loads(extracted_text)
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
        select(ProductAliases).join(Products).where(Products.restaurant_id == restaurant_id)
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
        response = chat_completions_create(
            settings=settings,
            messages=messages,
            temperature=0.2,
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise OpenAIError(f"Unexpected LLM response: {response}")

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

