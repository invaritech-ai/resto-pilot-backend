"""
Product alias management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.product_aliases import ProductAliases
from app.db.models.products import Products
from app.db.models.restaurant_user import RestaurantUser

from .base import has_restaurant_access, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_product_alias_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> dict[str, Tool]:
    """Create product alias management tools."""

    def list_product_aliases(args: dict[str, Any]) -> str:
        """List product aliases for a restaurant or a specific product."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        product_id_str = args.get("product_id", "").strip()
        product_id = uuid.UUID(product_id_str) if product_id_str else None

        query = (
            select(ProductAliases, Products)
            .join(Products, ProductAliases.product_id == Products.id)
            .where(Products.restaurant_id == restaurant_id)
        )

        if product_id:
            query = query.where(ProductAliases.product_id == product_id)

        results = db.execute(query).all()

        if not results:
            return "No product aliases found."

        result = []
        for alias, product in results:
            result.append(
                {
                    "id": str(alias.id),
                    "product_id": str(alias.product_id),
                    "product_name": product.name_en,
                    "alias_text": alias.alias_text,
                    "supplier_id": str(alias.supplier_id) if alias.supplier_id else None,
                    "confidence": alias.confidence,
                }
            )

        return json.dumps(result, indent=2)

    def create_product_alias(args: dict[str, Any]) -> str:
        """Create a product alias. Only restaurant owners can create aliases."""
        product_id_str = args.get("product_id", "").strip()
        if not product_id_str:
            return "Error: product_id is required."

        try:
            product_id = uuid.UUID(product_id_str)
        except ValueError:
            return "Error: Invalid product_id format."

        product = db.get(Products, product_id)
        if not product:
            return "Error: Product not found."

        if not is_restaurant_owner(db, user_id, product.restaurant_id):
            return "Error: Only restaurant owners can create product aliases."

        alias_text = args.get("alias_text", "").strip()
        if not alias_text:
            return "Error: alias_text is required."

        supplier_id_str = args.get("supplier_id", "").strip()
        supplier_id = uuid.UUID(supplier_id_str) if supplier_id_str else None

        confidence = args.get("confidence", "manual")
        if confidence not in ["auto", "manual"]:
            return "Error: confidence must be 'auto' or 'manual'."

        alias = ProductAliases(
            product_id=product_id,
            supplier_id=supplier_id,
            alias_text=alias_text,
            confidence=confidence,
        )
        db.add(alias)
        try:
            db.commit()
            return json.dumps(
                {
                    "status": "created",
                    "id": str(alias.id),
                    "alias_text": alias.alias_text,
                    "message": f"Alias '{alias.alias_text}' created for product '{product.name_en}'!",
                },
                indent=2,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_product_alias_failed")
            return f"Error creating alias: {str(e)}"

    def confirm_product_alias_match(args: dict[str, Any]) -> str:
        """Confirm an ambiguous product alias match from file processing staging."""
        staging_id_str = args.get("staging_id", "").strip()
        if not staging_id_str:
            return "Error: staging_id is required."

        try:
            staging_id = uuid.UUID(staging_id_str)
        except ValueError:
            return "Error: Invalid staging_id format."

        from app.db.models.file_processing_staging import FileProcessingStaging

        staging = db.get(FileProcessingStaging, staging_id)
        if not staging:
            return "Error: File processing staging record not found."

        if not has_restaurant_access(db, user_id, staging.restaurant_id):
            return "Error: You don't have access to this file processing record."

        supplier_name = args.get("supplier_name", "").strip()
        product_id_str = args.get("product_id", "").strip()
        if not supplier_name or not product_id_str:
            return "Error: supplier_name and product_id are required."

        try:
            product_id = uuid.UUID(product_id_str)
        except ValueError:
            return "Error: Invalid product_id format."

        product = db.get(Products, product_id)
        if not product:
            return "Error: Product not found."

        # Create alias with manual confidence
        alias = ProductAliases(
            product_id=product_id,
            supplier_id=staging.document_id,  # This should be supplier_id
            alias_text=supplier_name,
            confidence="manual",
        )
        db.add(alias)

        # Update staging record's product_alias_matches_json
        matches = staging.product_alias_matches_json.copy()
        # Update the match for this supplier_name
        for match in matches.get("matches", []):
            if match.get("supplier_name") == supplier_name:
                match["product_id"] = str(product_id)
                match["needs_confirmation"] = False
                break

        staging.product_alias_matches_json = matches
        try:
            db.commit()
            return f"Confirmed alias: '{supplier_name}' → '{product.name_en}'"
        except Exception as e:
            db.rollback()
            logger.exception("confirm_product_alias_match_failed")
            return f"Error confirming alias match: {str(e)}"

    return {
        "list_product_aliases": Tool(
            name="list_product_aliases",
            description="List product aliases for a restaurant or a specific product.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "product_id": {
                        "type": "string",
                        "description": "Product UUID to filter by (optional).",
                    },
                },
                "required": ["restaurant_id"],
                "additionalProperties": False,
            },
            handler=list_product_aliases,
        ),
        "create_product_alias": Tool(
            name="create_product_alias",
            description="Create a product alias. Only restaurant owners can create aliases.",
            parameters={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product's UUID.",
                    },
                    "alias_text": {
                        "type": "string",
                        "description": "The alias text (supplier's name for this product).",
                    },
                    "supplier_id": {
                        "type": "string",
                        "description": "Supplier UUID (optional).",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["auto", "manual"],
                        "description": "Confidence level: 'auto' for AI-matched, 'manual' for user-confirmed.",
                    },
                },
                "required": ["product_id", "alias_text"],
                "additionalProperties": False,
            },
            handler=create_product_alias,
        ),
        "confirm_product_alias_match": Tool(
            name="confirm_product_alias_match",
            description="Confirm an ambiguous product alias match from file processing staging.",
            parameters={
                "type": "object",
                "properties": {
                    "staging_id": {
                        "type": "string",
                        "description": "The file processing staging record UUID.",
                    },
                    "supplier_name": {
                        "type": "string",
                        "description": "The supplier product name to confirm.",
                    },
                    "product_id": {
                        "type": "string",
                        "description": "The product UUID to match it to.",
                    },
                },
                "required": ["staging_id", "supplier_name", "product_id"],
                "additionalProperties": False,
            },
            handler=confirm_product_alias_match,
        ),
    }

