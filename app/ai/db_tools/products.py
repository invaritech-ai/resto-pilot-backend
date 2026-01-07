"""
Product management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.products import Products
from app.db.models.restaurant_user import RestaurantUser

from .base import format_date, has_restaurant_access

logger = logging.getLogger(__name__)


def create_product_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> dict[str, Tool]:
    """Create product management tools."""

    def list_products(args: dict[str, Any]) -> str:
        """List all products for a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        products = db.scalars(
            select(Products).where(
                Products.restaurant_id == restaurant_id, Products.is_active == True
            )
        ).all()

        if not products:
            return "No products found for this restaurant."

        result = []
        for product in products:
            result.append(
                {
                    "id": str(product.id),
                    "name_en": product.name_en,
                    "name_local": product.name_local,
                    "category": product.category,
                    "sub_category": product.sub_category,
                    "storage_type": product.storage_type,
                    "default_unit": product.default_unit,
                }
            )

        return json.dumps(result, indent=2)

    def create_product(args: dict[str, Any]) -> str:
        """Create a new product. Only restaurant owners can create products."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        # Check if user is owner
        membership = db.scalar(
            select(RestaurantUser).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user_id,
                RestaurantUser.role == "owner",
                RestaurantUser.status == "active",
            )
        )
        if not membership:
            return "Error: Only restaurant owners can create products."

        name_en = args.get("name_en", "").strip()
        if not name_en:
            return "Error: name_en is required."

        product = Products(
            restaurant_id=restaurant_id,
            name_en=name_en,
            name_local=args.get("name_local") if args.get("name_local") else None,
            category=args.get("category") if args.get("category") else None,
            sub_category=args.get("sub_category") if args.get("sub_category") else None,
            storage_type=args.get("storage_type") if args.get("storage_type") else None,
            default_unit=args.get("default_unit") if args.get("default_unit") else None,
            default_unit_size=float(args.get("default_unit_size"))
            if args.get("default_unit_size")
            else None,
            is_active=True,
        )
        db.add(product)
        try:
            db.commit()
            return json.dumps(
                {
                    "status": "created",
                    "id": str(product.id),
                    "name_en": product.name_en,
                    "message": f"Product '{product.name_en}' created!",
                },
                indent=2,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_product_failed")
            return f"Error creating product: {str(e)}"

    def update_product(args: dict[str, Any]) -> str:
        """Update product details. Only restaurant owners can update."""
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

        if not has_restaurant_access(db, user_id, product.restaurant_id):
            return "Error: You don't have access to this product's restaurant."

        # Check if user is owner
        membership = db.scalar(
            select(RestaurantUser).where(
                RestaurantUser.restaurant_id == product.restaurant_id,
                RestaurantUser.user_id == user_id,
                RestaurantUser.role == "owner",
                RestaurantUser.status == "active",
            )
        )
        if not membership:
            return "Error: Only restaurant owners can update products."

        updates = []
        if "name_en" in args:
            product.name_en = args["name_en"].strip()
            updates.append(f"Name (EN): {product.name_en}")
        if "name_local" in args:
            product.name_local = args["name_local"].strip() if args["name_local"] else None
            updates.append(f"Name (Local): {product.name_local}")
        if "category" in args:
            product.category = args["category"] if args["category"] else None
            updates.append(f"Category: {product.category}")
        if "sub_category" in args:
            product.sub_category = args["sub_category"] if args["sub_category"] else None
            updates.append(f"Sub-category: {product.sub_category}")
        if "storage_type" in args:
            product.storage_type = args["storage_type"] if args["storage_type"] else None
            updates.append(f"Storage type: {product.storage_type}")
        if "default_unit" in args:
            product.default_unit = args["default_unit"] if args["default_unit"] else None
            updates.append(f"Default unit: {product.default_unit}")
        if "default_unit_size" in args:
            product.default_unit_size = (
                float(args["default_unit_size"]) if args["default_unit_size"] else None
            )
            updates.append(f"Default unit size: {product.default_unit_size}")
        if "is_active" in args:
            product.is_active = bool(args["is_active"])
            updates.append(f"Active: {product.is_active}")

        if not updates:
            return "Error: Provide at least one field to update."

        try:
            db.commit()
            return "Product updated:\n" + "\n".join(updates)
        except Exception as e:
            db.rollback()
            logger.exception("update_product_failed")
            return f"Error updating product: {str(e)}"

    def find_product_by_name(args: dict[str, Any]) -> str:
        """Search for a product by name within a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required."

        products = db.scalars(
            select(Products).where(
                Products.restaurant_id == restaurant_id, Products.is_active == True
            )
        ).all()

        name_lower = name.lower()
        matches = []
        for product in products:
            if (
                name_lower in product.name_en.lower()
                or (product.name_local and name_lower in product.name_local.lower())
            ):
                matches.append(
                    {
                        "id": str(product.id),
                        "name_en": product.name_en,
                        "name_local": product.name_local,
                        "category": product.category,
                    }
                )

        if not matches:
            return f"No product found matching '{name}'."

        return json.dumps(matches, indent=2)

    return {
        "list_products": Tool(
            name="list_products",
            description="List all products for a restaurant.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                },
                "required": ["restaurant_id"],
                "additionalProperties": False,
            },
            handler=list_products,
        ),
        "create_product": Tool(
            name="create_product",
            description="Create a new product. Only restaurant owners can create products.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "name_en": {
                        "type": "string",
                        "description": "Product name in English.",
                    },
                    "name_local": {
                        "type": "string",
                        "description": "Product name in local language (optional).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Product category (optional).",
                    },
                    "sub_category": {
                        "type": "string",
                        "description": "Product sub-category (optional).",
                    },
                    "storage_type": {
                        "type": "string",
                        "enum": ["frozen", "chilled", "dry"],
                        "description": "Storage type (optional).",
                    },
                    "default_unit": {
                        "type": "string",
                        "enum": ["kg", "pack", "piece"],
                        "description": "Default unit of measure (optional).",
                    },
                    "default_unit_size": {
                        "type": "number",
                        "description": "Default unit size (optional).",
                    },
                },
                "required": ["restaurant_id", "name_en"],
                "additionalProperties": False,
            },
            handler=create_product,
        ),
        "update_product": Tool(
            name="update_product",
            description="Update product details. Only restaurant owners can update.",
            parameters={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product's UUID.",
                    },
                    "name_en": {"type": "string", "description": "Product name in English."},
                    "name_local": {
                        "type": "string",
                        "description": "Product name in local language.",
                    },
                    "category": {"type": "string", "description": "Product category."},
                    "sub_category": {"type": "string", "description": "Product sub-category."},
                    "storage_type": {
                        "type": "string",
                        "enum": ["frozen", "chilled", "dry"],
                        "description": "Storage type.",
                    },
                    "default_unit": {
                        "type": "string",
                        "enum": ["kg", "pack", "piece"],
                        "description": "Default unit of measure.",
                    },
                    "default_unit_size": {
                        "type": "number",
                        "description": "Default unit size.",
                    },
                    "is_active": {
                        "type": "boolean",
                        "description": "Whether the product is active.",
                    },
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
            handler=update_product,
        ),
        "find_product_by_name": Tool(
            name="find_product_by_name",
            description="Search for a product by name within a restaurant.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "name": {
                        "type": "string",
                        "description": "The product name to search for.",
                    },
                },
                "required": ["restaurant_id", "name"],
                "additionalProperties": False,
            },
            handler=find_product_by_name,
        ),
    }

