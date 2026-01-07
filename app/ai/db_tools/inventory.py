"""
Inventory management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.db.models.inventory_batches import InventoryBatches
from app.db.models.inventory_movements import InventoryMovements
from app.db.models.restaurant_user import RestaurantUser

from .base import format_date, has_restaurant_access, is_restaurant_owner

logger = logging.getLogger(__name__)


def create_inventory_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> dict[str, Tool]:
    """Create inventory management tools."""

    def list_inventory(args: dict[str, Any]) -> str:
        """List inventory batches for a restaurant, optionally filtered by product."""
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

        query = select(InventoryBatches).where(InventoryBatches.restaurant_id == restaurant_id)

        if product_id:
            query = query.where(InventoryBatches.product_id == product_id)

        batches = db.scalars(query).all()

        if not batches:
            return "No inventory batches found."

        result = []
        for batch in batches:
            result.append(
                {
                    "id": str(batch.id),
                    "product_id": str(batch.product_id),
                    "quantity": float(batch.quantity),
                    "unit": batch.unit,
                    "unit_cost": float(batch.unit_cost),
                    "received_date": format_date(batch.received_date),
                    "expiry_date": format_date(batch.expiry_date),
                    "status": batch.status,
                }
            )

        return json.dumps(result, indent=2)

    def get_inventory_batch(args: dict[str, Any]) -> str:
        """Get details of a specific inventory batch."""
        batch_id_str = args.get("batch_id", "").strip()
        if not batch_id_str:
            return "Error: batch_id is required."

        try:
            batch_id = uuid.UUID(batch_id_str)
        except ValueError:
            return "Error: Invalid batch_id format."

        batch = db.get(InventoryBatches, batch_id)
        if not batch:
            return "Error: Inventory batch not found."

        if not has_restaurant_access(db, user_id, batch.restaurant_id):
            return "Error: You don't have access to this inventory batch."

        return json.dumps(
            {
                "id": str(batch.id),
                "product_id": str(batch.product_id),
                "supplier_id": str(batch.supplier_id),
                "quantity": float(batch.quantity),
                "unit": batch.unit,
                "unit_cost": float(batch.unit_cost),
                "received_date": format_date(batch.received_date),
                "expiry_date": format_date(batch.expiry_date),
                "status": batch.status,
                "created_at": format_date(batch.created_at),
            },
            indent=2,
        )

    def create_inventory_movement(args: dict[str, Any]) -> str:
        """Record an inventory movement (receive, consume, waste, adjust)."""
        batch_id_str = args.get("batch_id", "").strip()
        if not batch_id_str:
            return "Error: batch_id is required."

        try:
            batch_id = uuid.UUID(batch_id_str)
        except ValueError:
            return "Error: Invalid batch_id format."

        batch = db.get(InventoryBatches, batch_id)
        if not batch:
            return "Error: Inventory batch not found."

        if not has_restaurant_access(db, user_id, batch.restaurant_id):
            return "Error: You don't have access to this inventory batch."

        movement_type = args.get("movement_type", "").strip()
        if movement_type not in ["receive", "consume", "waste", "adjust"]:
            return "Error: movement_type must be one of: receive, consume, waste, adjust."

        quantity = args.get("quantity")
        if quantity is None:
            return "Error: quantity is required."

        try:
            quantity = float(quantity)
        except (ValueError, TypeError):
            return "Error: quantity must be a number."

        reason = args.get("reason", "").strip() if args.get("reason") else None

        movement = InventoryMovements(
            restaurant_id=batch.restaurant_id,
            inventory_batch_id=batch_id,
            movement_type=movement_type,
            quantity=quantity,
            reason=reason,
        )
        db.add(movement)

        # Update batch quantity if consuming/wasting
        if movement_type in ["consume", "waste"]:
            batch.quantity = max(0, float(batch.quantity) - quantity)
            if batch.quantity == 0:
                batch.status = "consumed"

        try:
            db.commit()
            return json.dumps(
                {
                    "status": "created",
                    "id": str(movement.id),
                    "movement_type": movement_type,
                    "quantity": quantity,
                    "message": f"Inventory movement recorded: {movement_type} {quantity} {batch.unit}",
                },
                indent=2,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_inventory_movement_failed")
            return f"Error creating inventory movement: {str(e)}"

    return {
        "list_inventory": Tool(
            name="list_inventory",
            description="List inventory batches for a restaurant, optionally filtered by product.",
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
            handler=list_inventory,
        ),
        "get_inventory_batch": Tool(
            name="get_inventory_batch",
            description="Get details of a specific inventory batch by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "batch_id": {
                        "type": "string",
                        "description": "The inventory batch's UUID.",
                    },
                },
                "required": ["batch_id"],
                "additionalProperties": False,
            },
            handler=get_inventory_batch,
        ),
        "create_inventory_movement": Tool(
            name="create_inventory_movement",
            description="Record an inventory movement (receive, consume, waste, adjust).",
            parameters={
                "type": "object",
                "properties": {
                    "batch_id": {
                        "type": "string",
                        "description": "The inventory batch's UUID.",
                    },
                    "movement_type": {
                        "type": "string",
                        "enum": ["receive", "consume", "waste", "adjust"],
                        "description": "Type of movement.",
                    },
                    "quantity": {
                        "type": "number",
                        "description": "Quantity for this movement.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the movement (optional).",
                    },
                },
                "required": ["batch_id", "movement_type", "quantity"],
                "additionalProperties": False,
            },
            handler=create_inventory_movement,
        ),
    }

