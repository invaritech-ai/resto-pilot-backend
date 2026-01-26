"""
Supplier management tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.ai.tools import Tool
from app.conversation import responses
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.suppliers import Suppliers, normalize_supplier_name
from app.db.models.restaurant import Restaurant

from .base import format_date, has_restaurant_access

logger = logging.getLogger(__name__)


def create_supplier_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
    pending_action: dict[str, Any] | None = None,
    user_message: str | None = None,
) -> dict[str, Tool]:
    """Create supplier management tools."""

    def _normalize(text: str | None) -> str:
        return text.strip().lower() if isinstance(text, str) else ""

    def _is_confirm(text: str) -> bool:
        tokens = [token.strip(".,!?") for token in text.split()]
        return any(
            token in {"yes", "confirm", "ok", "okay", "proceed", "sure"}
            for token in tokens
        ) or "do it" in text

    def _is_cancel(text: str) -> bool:
        tokens = [token.strip(".,!?") for token in text.split()]
        return any(
            token in {"no", "cancel", "nevermind", "stop", "don't", "dont"}
            for token in tokens
        ) or "never mind" in text

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

        rows = db.execute(
            select(Suppliers, RestaurantSuppliers)
            .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
            .where(
                RestaurantSuppliers.restaurant_id == restaurant_id,
                RestaurantSuppliers.status == "active",
                Suppliers.is_active == True,
            )
            .order_by(Suppliers.name.asc())
        ).all()
        restaurant = db.get(Restaurant, restaurant_id)

        result = []
        for supplier, link in rows:
            result.append(
                {
                    "id": str(supplier.id),
                    "name": supplier.name,
                    "currency": link.default_currency or supplier.currency,
                    "language": supplier.language,
                    "lead_time_days": link.lead_time_days or supplier.lead_time_days,
                    "status": link.status,
                }
            )

        payload = {
            "restaurant_name": restaurant.name if restaurant else None,
            "suppliers": result,
        }
        return json.dumps(payload, indent=2)

    def list_my_suppliers(_args: dict[str, Any]) -> str:
        """List all suppliers visible to the user (across outlets).

        Includes:
        - User-owned suppliers (even if unlinked)
        - Legacy suppliers with NULL user_id, if linked to an outlet the user can access
        """
        rows = db.execute(
            select(Suppliers, RestaurantSuppliers, Restaurant, RestaurantUser)
            .join(
                RestaurantSuppliers,
                RestaurantSuppliers.supplier_id == Suppliers.id,
                isouter=True,
            )
            .join(
                Restaurant,
                Restaurant.id == RestaurantSuppliers.restaurant_id,
                isouter=True,
            )
            .join(
                RestaurantUser,
                and_(
                    RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                    RestaurantUser.user_id == user_id,
                ),
                isouter=True,
            )
            .where(
                Suppliers.is_active == True,
                (Suppliers.user_id == user_id)
                | (
                    Suppliers.user_id.is_(None)
                    & (RestaurantUser.user_id.is_not(None))
                    & (RestaurantUser.status != "removed")
                ),
            )
            .order_by(Suppliers.name.asc())
        ).all()

        suppliers: dict[str, dict[str, Any]] = {}
        for supplier, link, restaurant, membership in rows:
            supplier_key = str(supplier.id)
            entry = suppliers.get(supplier_key)
            if not entry:
                entry = {
                    "id": supplier_key,
                    "name": supplier.name,
                    "currency": supplier.currency,
                    "contact_name": supplier.contact_name,
                    "contact_email": supplier.contact_email,
                    "contact_phone": supplier.contact_phone,
                    "linked_outlets": [],
                }
                suppliers[supplier_key] = entry

            if link and restaurant and membership and membership.status != "removed":
                entry["linked_outlets"].append(
                    {
                        "restaurant_id": str(restaurant.id),
                        "restaurant_name": restaurant.name,
                        "status": link.status,
                        "default_currency": link.default_currency,
                        "lead_time_days": link.lead_time_days,
                        "notes": link.notes,
                    }
                )

        return json.dumps({"suppliers": list(suppliers.values())}, indent=2)

    def list_unlinked_suppliers(args: dict[str, Any]) -> str:
        """List user-owned suppliers not linked to an outlet (or not linked to a specific outlet)."""
        restaurant_id_str = str(args.get("restaurant_id") or "").strip()
        restaurant_id: uuid.UUID | None = None
        restaurant_name: str | None = None
        if restaurant_id_str:
            try:
                restaurant_id = uuid.UUID(restaurant_id_str)
            except ValueError:
                return "Error: Invalid restaurant_id format."
            if not has_restaurant_access(db, user_id, restaurant_id):
                return "Error: You don't have access to this restaurant."
            restaurant = db.get(Restaurant, restaurant_id)
            restaurant_name = restaurant.name if restaurant else None

        if restaurant_id:
            linked_supplier_ids = set(
                db.scalars(
                    select(RestaurantSuppliers.supplier_id).where(
                        RestaurantSuppliers.restaurant_id == restaurant_id,
                    )
                ).all()
            )
        else:
            linked_supplier_ids = set(
                db.scalars(
                    select(RestaurantSuppliers.supplier_id)
                    .join(
                        RestaurantUser,
                        RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                    )
                    .where(
                        RestaurantUser.user_id == user_id,
                        RestaurantUser.status != "removed",
                    )
                ).all()
            )

        conditions = [
            Suppliers.user_id == user_id,
            Suppliers.is_active == True,
        ]
        if linked_supplier_ids:
            conditions.append(~Suppliers.id.in_(linked_supplier_ids))

        suppliers = db.scalars(select(Suppliers).where(*conditions).order_by(Suppliers.name.asc())).all()

        return json.dumps(
            {
                "restaurant_id": str(restaurant_id) if restaurant_id else None,
                "restaurant_name": restaurant_name,
                "unlinked_suppliers": [
                    {
                        "id": str(supplier.id),
                        "name": supplier.name,
                        "currency": supplier.currency,
                        "contact_name": supplier.contact_name,
                        "contact_email": supplier.contact_email,
                        "contact_phone": supplier.contact_phone,
                    }
                    for supplier in suppliers
                ],
            },
            indent=2,
        )

    def create_supplier(args: dict[str, Any]) -> str:
        """Create a new supplier for a restaurant."""
        message_lower = _normalize(user_message)

        restaurant_id_str = args.get("restaurant_id", "").strip()
        name = args.get("name", "").strip()
        currency = args.get("currency")
        language = args.get("language")
        lead_time_days = args.get("lead_time_days")
        notes = args.get("notes")
        account_number = args.get("account_number")

        if pending_action and pending_action.get("type") == "create_supplier":
            if _is_cancel(message_lower):
                return json.dumps(
                    {
                        "status": "cancelled",
                        "message": responses.SUPPLIER_CREATE_CANCELLED,
                        "context_update": {"clear_pending_action": True},
                    },
                    indent=2,
                )
            if _is_confirm(message_lower):
                pending_restaurant_id = pending_action.get("restaurant_id")
                pending_name = pending_action.get("name")
                pending_currency = pending_action.get("currency")
                pending_language = pending_action.get("language")
                pending_lead_time = pending_action.get("lead_time_days")
                pending_notes = pending_action.get("notes")
                pending_account_number = pending_action.get("account_number")
                if not pending_restaurant_id or not pending_name:
                    return "Error: Pending supplier details are incomplete."
                try:
                    restaurant_id = uuid.UUID(str(pending_restaurant_id))
                except ValueError:
                    return "Error: Invalid pending restaurant_id format."
                name = str(pending_name).strip()
                if not has_restaurant_access(db, user_id, restaurant_id):
                    return "Error: You don't have access to this restaurant."

                normalized = normalize_supplier_name(name)
                supplier = db.scalar(
                    select(Suppliers).where(
                        Suppliers.user_id == user_id,
                        Suppliers.name_normalized == normalized,
                        Suppliers.is_active == True,
                    )
                )
                if not supplier:
                    supplier = Suppliers(
                        user_id=user_id,
                        name=name,
                        name_normalized=normalized,
                        language=pending_language,
                        is_active=True,
                    )
                    db.add(supplier)
                    db.flush()
                else:
                    if not supplier.name_normalized:
                        supplier.name_normalized = normalized
                    if pending_language and not supplier.language:
                        supplier.language = pending_language
                    if pending_notes and not supplier.notes:
                        supplier.notes = pending_notes

                link = db.scalar(
                    select(RestaurantSuppliers).where(
                        RestaurantSuppliers.restaurant_id == restaurant_id,
                        RestaurantSuppliers.supplier_id == supplier.id,
                    )
                )
                if not link:
                    link = RestaurantSuppliers(
                        restaurant_id=restaurant_id,
                        supplier_id=supplier.id,
                        status="active",
                        account_number=pending_account_number,
                        default_currency=pending_currency,
                        lead_time_days=pending_lead_time,
                        notes=pending_notes,
                    )
                    db.add(link)
                else:
                    if pending_account_number and not link.account_number:
                        link.account_number = pending_account_number
                    if pending_currency and not link.default_currency:
                        link.default_currency = pending_currency
                    if pending_lead_time is not None and link.lead_time_days is None:
                        link.lead_time_days = pending_lead_time
                    if pending_notes and not link.notes:
                        link.notes = pending_notes
                try:
                    db.commit()
                    return json.dumps(
                        {
                            "status": "created",
                            "id": str(supplier.id),
                            "name": supplier.name,
                            "message": responses.SUPPLIER_CREATED.format(name=supplier.name),
                            "context_update": {
                                "clear_pending_action": True,
                                "active_restaurant_id": str(restaurant_id),
                                "active_supplier_id": str(supplier.id),
                            },
                        },
                        indent=2,
                    )
                except Exception as e:
                    db.rollback()
                    logger.exception("create_supplier_failed")
                    return f"Error creating supplier: {str(e)}"

            if not restaurant_id_str and not name:
                return json.dumps(
                    {
                        "status": "pending_confirmation",
                        "message": responses.SUPPLIER_CREATE_CONFIRMATION.format(
                            name=pending_action.get("name"),
                            restaurant=pending_action.get("restaurant_name"),
                        ),
                        "context_update": {
                            "pending_action": pending_action,
                            "active_restaurant_id": pending_action.get("restaurant_id"),
                        },
                    },
                    indent=2,
                )
            if not restaurant_id_str and pending_action.get("restaurant_id"):
                restaurant_id_str = str(pending_action.get("restaurant_id"))
            if not name and pending_action.get("name"):
                name = str(pending_action.get("name")).strip()

        if not restaurant_id_str:
            return "Error: restaurant_id is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        if not name:
            return "Error: name is required."

        restaurant = db.get(Restaurant, restaurant_id)
        restaurant_name = restaurant.name if restaurant else "your outlet"

        return json.dumps(
            {
                "status": "pending_confirmation",
                "message": responses.SUPPLIER_CREATE_CONFIRMATION.format(
                    name=name,
                    restaurant=restaurant_name,
                ),
                "context_update": {
                    "pending_action": {
                        "type": "create_supplier",
                        "restaurant_id": str(restaurant_id),
                        "restaurant_name": restaurant_name,
                        "name": name,
                        "currency": currency,
                        "language": language,
                        "lead_time_days": lead_time_days,
                        "notes": notes,
                        "account_number": account_number,
                    },
                    "active_restaurant_id": str(restaurant_id),
                },
            },
            indent=2,
        )

    def link_supplier_to_restaurant(args: dict[str, Any]) -> str:
        """Link an existing supplier to a restaurant/outlet (creates or updates restaurant_suppliers)."""
        restaurant_id_str = str(args.get("restaurant_id", "")).strip()
        supplier_id_str = str(args.get("supplier_id", "")).strip()
        supplier_name = str(args.get("supplier_name", "")).strip()

        if not restaurant_id_str:
            return "Error: restaurant_id is required."
        if not supplier_id_str and not supplier_name:
            return "Error: supplier_id or supplier_name is required."

        try:
            restaurant_id = uuid.UUID(restaurant_id_str)
        except ValueError:
            return "Error: Invalid restaurant_id format."

        if not has_restaurant_access(db, user_id, restaurant_id):
            return "Error: You don't have access to this restaurant."

        supplier: Suppliers | None = None
        if supplier_id_str:
            try:
                supplier_id = uuid.UUID(supplier_id_str)
            except ValueError:
                return "Error: Invalid supplier_id format."
            supplier = db.get(Suppliers, supplier_id)
        else:
            normalized = normalize_supplier_name(supplier_name)
            matches = db.scalars(
                select(Suppliers).where(
                    Suppliers.user_id == user_id,
                    Suppliers.name_normalized == normalized,
                    Suppliers.is_active == True,
                )
            ).all()
            if not matches:
                return "Error: Supplier not found."
            if len(matches) > 1:
                return "Error: Multiple suppliers match that name. Please specify supplier_id."
            supplier = matches[0]

        if not supplier:
            return "Error: Supplier not found."
        if not supplier.is_active:
            return "Error: Supplier is inactive."

        # Enforce ownership. Legacy suppliers may have NULL user_id; claim them on first link.
        if supplier.user_id is None:
            supplier.user_id = user_id
        elif supplier.user_id != user_id:
            return "Error: Supplier does not belong to this user."

        status_value = str(args.get("status_value") or args.get("status") or "active").strip().lower()
        if status_value not in {"active", "inactive"}:
            return "Error: status_value must be 'active' or 'inactive'."

        account_number = args.get("account_number")
        default_currency = args.get("default_currency")
        lead_time_days = args.get("lead_time_days")
        notes = args.get("notes")

        link = db.scalar(
            select(RestaurantSuppliers).where(
                RestaurantSuppliers.restaurant_id == restaurant_id,
                RestaurantSuppliers.supplier_id == supplier.id,
            )
        )
        if not link:
            link = RestaurantSuppliers(
                restaurant_id=restaurant_id,
                supplier_id=supplier.id,
                status=status_value,
                account_number=account_number,
                default_currency=default_currency,
                lead_time_days=int(lead_time_days) if lead_time_days is not None else None,
                notes=notes,
            )
            db.add(link)
        else:
            link.status = status_value
            if account_number is not None:
                link.account_number = account_number
            if default_currency is not None:
                link.default_currency = default_currency
            if lead_time_days is not None:
                link.lead_time_days = int(lead_time_days)
            if notes is not None:
                link.notes = notes

        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.exception("link_supplier_to_restaurant_failed")
            return f"Error linking supplier: {str(e)}"

        restaurant = db.get(Restaurant, restaurant_id)
        return json.dumps(
            {
                "status": "ok",
                "supplier_id": str(supplier.id),
                "supplier_name": supplier.name,
                "restaurant_id": str(restaurant_id),
                "restaurant_name": restaurant.name if restaurant else None,
                "link": {
                    "status": link.status,
                    "account_number": link.account_number,
                    "default_currency": link.default_currency,
                    "lead_time_days": link.lead_time_days,
                    "notes": link.notes,
                },
                "context_update": {
                    "active_restaurant_id": str(restaurant_id),
                    "active_supplier_id": str(supplier.id),
                },
            },
            indent=2,
        )

    def get_supplier(args: dict[str, Any]) -> str:
        """Get details of a specific supplier."""
        supplier_id_str = args.get("supplier_id", "").strip()
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not supplier_id_str:
            return "Error: supplier_id is required."

        try:
            supplier_id = uuid.UUID(supplier_id_str)
        except ValueError:
            return "Error: Invalid supplier_id format."

        supplier = db.get(Suppliers, supplier_id)
        if not supplier:
            return "Error: Supplier not found."

        link = None
        if restaurant_id_str:
            try:
                restaurant_id = uuid.UUID(restaurant_id_str)
            except ValueError:
                return "Error: Invalid restaurant_id format."
            if not has_restaurant_access(db, user_id, restaurant_id):
                return "Error: You don't have access to this restaurant."
            link = db.scalar(
                select(RestaurantSuppliers).where(
                    RestaurantSuppliers.restaurant_id == restaurant_id,
                    RestaurantSuppliers.supplier_id == supplier.id,
                )
            )
            if not link:
                return "Error: Supplier is not linked to this restaurant."
        else:
            link = db.scalar(
                select(RestaurantSuppliers)
                .join(
                    RestaurantUser,
                    RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                )
                .where(
                    RestaurantSuppliers.supplier_id == supplier.id,
                    RestaurantUser.user_id == user_id,
                    RestaurantUser.status != "removed",
                )
            )
            if not link:
                return "Error: You don't have access to this supplier."

        return json.dumps(
            {
                "id": str(supplier.id),
                "name": supplier.name,
                "currency": link.default_currency or supplier.currency if link else supplier.currency,
                "language": supplier.language,
                "lead_time_days": link.lead_time_days or supplier.lead_time_days if link else supplier.lead_time_days,
                "notes": link.notes if link and link.notes else supplier.notes,
                "account_number": link.account_number if link else None,
                "status": link.status if link else None,
                "is_active": supplier.is_active,
                "created_at": format_date(supplier.created_at),
            },
            indent=2,
        )

    def update_supplier(args: dict[str, Any]) -> str:
        """Update supplier details."""
        supplier_id_str = args.get("supplier_id", "").strip()
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not supplier_id_str:
            return "Error: supplier_id is required."

        try:
            supplier_id = uuid.UUID(supplier_id_str)
        except ValueError:
            return "Error: Invalid supplier_id format."

        supplier = db.get(Suppliers, supplier_id)
        if not supplier:
            return "Error: Supplier not found."

        link = None
        if restaurant_id_str:
            try:
                restaurant_id = uuid.UUID(restaurant_id_str)
            except ValueError:
                return "Error: Invalid restaurant_id format."
            if not has_restaurant_access(db, user_id, restaurant_id):
                return "Error: You don't have access to this restaurant."
            link = db.scalar(
                select(RestaurantSuppliers).where(
                    RestaurantSuppliers.restaurant_id == restaurant_id,
                    RestaurantSuppliers.supplier_id == supplier.id,
                )
            )
            if not link:
                return "Error: Supplier is not linked to this restaurant."
        else:
            link = db.scalar(
                select(RestaurantSuppliers)
                .join(
                    RestaurantUser,
                    RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                )
                .where(
                    RestaurantSuppliers.supplier_id == supplier.id,
                    RestaurantUser.user_id == user_id,
                    RestaurantUser.status != "removed",
                )
            )
            if not link:
                return "Error: You don't have access to this supplier."

        updates = []
        if "name" in args:
            supplier.name = args["name"].strip()
            supplier.name_normalized = normalize_supplier_name(supplier.name)
            updates.append(f"Name: {supplier.name}")
        if "currency" in args:
            if link:
                link.default_currency = args["currency"] if args["currency"] else None
                updates.append(f"Currency: {link.default_currency}")
            else:
                supplier.currency = args["currency"] if args["currency"] else None
                updates.append(f"Currency: {supplier.currency}")
        if "language" in args:
            supplier.language = args["language"] if args["language"] else None
            updates.append(f"Language: {supplier.language}")
        if "lead_time_days" in args:
            if link:
                link.lead_time_days = (
                    int(args["lead_time_days"]) if args["lead_time_days"] else None
                )
                updates.append(f"Lead time (days): {link.lead_time_days}")
            else:
                supplier.lead_time_days = (
                    int(args["lead_time_days"]) if args["lead_time_days"] else None
                )
                updates.append(f"Lead time (days): {supplier.lead_time_days}")
        if "notes" in args:
            if link:
                link.notes = args["notes"] if args["notes"] else None
                updates.append(f"Notes: {link.notes}")
            else:
                supplier.notes = args["notes"] if args["notes"] else None
                updates.append(f"Notes: {supplier.notes}")
        if "account_number" in args and link:
            link.account_number = args["account_number"] if args["account_number"] else None
            updates.append(f"Account number: {link.account_number}")
        if "status" in args and link:
            link.status = args["status"] if args["status"] else link.status
            updates.append(f"Status: {link.status}")
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
            description="List suppliers linked to a specific outlet/restaurant. Returns JSON with restaurant_name and suppliers.",
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
        "list_my_suppliers": Tool(
            name="list_my_suppliers",
            description="List all suppliers across your outlets (and any you added but haven't linked yet), including legacy suppliers with NULL user_id that are linked to your outlets. Returns JSON.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_my_suppliers,
        ),
        "list_unlinked_suppliers": Tool(
            name="list_unlinked_suppliers",
            description="List your suppliers that are not linked to an outlet (or not linked to a specific outlet if restaurant_id provided). Returns JSON.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "Optional restaurant/outlet UUID to show suppliers not linked to that outlet.",
                    }
                },
                "additionalProperties": False,
            },
            handler=list_unlinked_suppliers,
        ),
        "link_supplier_to_restaurant": Tool(
            name="link_supplier_to_restaurant",
            description="Link an existing supplier to an outlet/restaurant (supports linking the same supplier to multiple outlets).",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_id": {
                        "type": "string",
                        "description": "The restaurant/outlet UUID.",
                    },
                    "supplier_id": {
                        "type": "string",
                        "description": "Supplier UUID (preferred if known).",
                    },
                    "supplier_name": {
                        "type": "string",
                        "description": "Supplier name (used to look up your supplier if supplier_id not provided).",
                    },
                    "status_value": {
                        "type": "string",
                        "description": "Link status: active or inactive (optional).",
                    },
                    "account_number": {
                        "type": "string",
                        "description": "Restaurant-specific account number (optional).",
                    },
                    "default_currency": {
                        "type": "string",
                        "description": "Restaurant-specific default currency (optional).",
                    },
                    "lead_time_days": {
                        "type": "integer",
                        "description": "Restaurant-specific lead time in days (optional).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Restaurant-specific notes (optional).",
                    },
                },
                "required": ["restaurant_id"],
                "additionalProperties": False,
            },
            handler=link_supplier_to_restaurant,
        ),
        "create_supplier": Tool(
            name="create_supplier",
            description="Create a new supplier for a restaurant.",
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
                    "account_number": {
                        "type": "string",
                        "description": "Account number (optional).",
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
                    "restaurant_id": {
                        "type": "string",
                        "description": "Restaurant UUID for scoped details (optional).",
                    },
                },
                "required": ["supplier_id"],
                "additionalProperties": False,
            },
            handler=get_supplier,
        ),
        "update_supplier": Tool(
            name="update_supplier",
            description="Update supplier details for a supplier.",
            parameters={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "string",
                        "description": "The supplier's UUID.",
                    },
                    "restaurant_id": {
                        "type": "string",
                        "description": "Restaurant UUID for scoped updates (optional).",
                    },
                    "name": {"type": "string", "description": "Supplier name (optional)."},
                    "currency": {
                        "type": "string",
                        "description": "Currency code (e.g., USD, EUR) (optional).",
                    },
                    "language": {"type": "string", "description": "Language code (optional)."},
                    "lead_time_days": {
                        "type": "integer",
                        "description": "Lead time in days (optional).",
                    },
                    "notes": {"type": "string", "description": "Additional notes (optional)."},
                    "account_number": {
                        "type": "string",
                        "description": "Account number (optional).",
                    },
                    "status": {
                        "type": "string",
                        "description": "Restaurant supplier status (optional).",
                    },
                    "is_active": {
                        "type": "boolean",
                        "description": "Whether the supplier is active (optional).",
                    },
                },
                "required": ["supplier_id"],
                "additionalProperties": False,
            },
            handler=update_supplier,
        ),
    }
