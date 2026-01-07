"""
Supplier management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.suppliers import Suppliers
from app.db.models.restaurant_user import RestaurantUser

from .base import format_date, has_restaurant_access, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_supplier_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> dict[str, Tool]:
    """Create supplier management tools."""

    def list_suppliers(args: dict[str, Any]) -> str:
        """List all suppliers for a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        suppliers = db.scalars(
            select(Suppliers).where(
                Suppliers.restaurant_id == restaurant_id, Suppliers.is_active == True
            )
        ).all()

        if not suppliers:
            return "No suppliers found for this restaurant."

        result = []
        for supplier in suppliers:
            result.append(
                {
                    "id": str(supplier.id),
                    "name": supplier.name,
                    "currency": supplier.currency,
                    "language": supplier.language,
                    "lead_time_days": supplier.lead_time_days,
                }
            )

        return json.dumps(result, indent=2)

    def create_supplier(args: dict[str, Any]) -> str:
        """Create a new supplier. Only restaurant owners can create suppliers."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not is_restaurant_owner(db, user_id, restaurant_id):
            return "Error: Only restaurant owners can create suppliers."

        name = args.get("name", "").strip()
        if not name:
            return "Error: name is required."

        supplier = Suppliers(
            restaurant_id=restaurant_id,
            name=name,
            language=args.get("language") if args.get("language") else None,
            currency=args.get("currency") if args.get("currency") else None,
            lead_time_days=int(args.get("lead_time_days"))
            if args.get("lead_time_days")
            else None,
            notes=args.get("notes") if args.get("notes") else None,
            is_active=True,
        )
        db.add(supplier)
        try:
            db.commit()
            return json.dumps(
                {
                    "status": "created",
                    "id": str(supplier.id),
                    "name": supplier.name,
                    "message": f"Supplier '{supplier.name}' created!",
                },
                indent=2,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_supplier_failed")
            return f"Error creating supplier: {str(e)}"

    def get_supplier(args: dict[str, Any]) -> str:
        """Get details of a specific supplier."""
        supplier_id_str = args.get("supplier_id", "").strip()
        if not supplier_id_str:
            return "Error: supplier_id is required."

        try:
            supplier_id = uuid.UUID(supplier_id_str)
        except ValueError:
            return "Error: Invalid supplier_id format."

        supplier = db.get(Suppliers, supplier_id)
        if not supplier:
            return "Error: Supplier not found."

        if not has_restaurant_access(db, user_id, supplier.restaurant_id):
            return "Error: You don't have access to this supplier's restaurant."

        return json.dumps(
            {
                "id": str(supplier.id),
                "name": supplier.name,
                "currency": supplier.currency,
                "language": supplier.language,
                "lead_time_days": supplier.lead_time_days,
                "notes": supplier.notes,
                "is_active": supplier.is_active,
                "created_at": format_date(supplier.created_at),
            },
            indent=2,
        )

    def update_supplier(args: dict[str, Any]) -> str:
        """Update supplier details. Only restaurant owners can update."""
        supplier_id_str = args.get("supplier_id", "").strip()
        if not supplier_id_str:
            return "Error: supplier_id is required."

        try:
            supplier_id = uuid.UUID(supplier_id_str)
        except ValueError:
            return "Error: Invalid supplier_id format."

        supplier = db.get(Suppliers, supplier_id)
        if not supplier:
            return "Error: Supplier not found."

        if not is_restaurant_owner(db, user_id, supplier.restaurant_id):
            return "Error: Only restaurant owners can update suppliers."

        updates = []
        if "name" in args:
            supplier.name = args["name"].strip()
            updates.append(f"Name: {supplier.name}")
        if "currency" in args:
            supplier.currency = args["currency"] if args["currency"] else None
            updates.append(f"Currency: {supplier.currency}")
        if "language" in args:
            supplier.language = args["language"] if args["language"] else None
            updates.append(f"Language: {supplier.language}")
        if "lead_time_days" in args:
            supplier.lead_time_days = (
                int(args["lead_time_days"]) if args["lead_time_days"] else None
            )
            updates.append(f"Lead time (days): {supplier.lead_time_days}")
        if "notes" in args:
            supplier.notes = args["notes"] if args["notes"] else None
            updates.append(f"Notes: {supplier.notes}")
        if "is_active" in args:
            supplier.is_active = bool(args["is_active"])
            updates.append(f"Active: {supplier.is_active}")

        if not updates:
            return "Error: Provide at least one field to update."

        try:
            db.commit()
            return "Supplier updated:\n" + "\n".join(updates)
        except Exception as e:
            db.rollback()
            logger.exception("update_supplier_failed")
            return f"Error updating supplier: {str(e)}"

    return {
        "list_suppliers": Tool(
            name="list_suppliers",
            description="List all suppliers for a restaurant.",
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
            handler=list_suppliers,
        ),
        "create_supplier": Tool(
            name="create_supplier",
            description="Create a new supplier. Only restaurant owners can create suppliers.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant's UUID.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Supplier name.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Currency code (e.g., USD, EUR) (optional).",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code (optional).",
                    },
                    "lead_time_days": {
                        "type": "integer",
                        "description": "Lead time in days (optional).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Additional notes (optional).",
                    },
                },
                "required": ["restaurant_id", "name"],
                "additionalProperties": False,
            },
            handler=create_supplier,
        ),
        "get_supplier": Tool(
            name="get_supplier",
            description="Get details of a specific supplier by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "string",
                        "description": "The supplier's UUID.",
                    },
                },
                "required": ["supplier_id"],
                "additionalProperties": False,
            },
            handler=get_supplier,
        ),
        "update_supplier": Tool(
            name="update_supplier",
            description="Update supplier details. Only restaurant owners can update.",
            parameters={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "string",
                        "description": "The supplier's UUID.",
                    },
                    "name": {"type": "string", "description": "Supplier name."},
                    "currency": {
                        "type": "string",
                        "description": "Currency code (e.g., USD, EUR).",
                    },
                    "language": {"type": "string", "description": "Language code."},
                    "lead_time_days": {
                        "type": "integer",
                        "description": "Lead time in days.",
                    },
                    "notes": {"type": "string", "description": "Additional notes."},
                    "is_active": {
                        "type": "boolean",
                        "description": "Whether the supplier is active.",
                    },
                },
                "required": ["supplier_id"],
                "additionalProperties": False,
            },
            handler=update_supplier,
        ),
    }

