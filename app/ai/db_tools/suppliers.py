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

    def _split_csv(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

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
                            restaurant_name=pending_action.get("restaurant_name"),
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
                    restaurant_name=restaurant_name,
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

    def link_suppliers(args: dict[str, Any]) -> str:
        """Link supplier(s) (create by name if needed) to outlet(s) (or all outlets)."""
        message_lower = _normalize(user_message)

        if pending_action and pending_action.get("type") == "link_suppliers":
            if _is_cancel(message_lower):
                return json.dumps(
                    {
                        "status": "cancelled",
                        "message": responses.SUPPLIER_LINK_MULTI_SUPPLIERS_CANCELLED,
                        "context_update": {"clear_pending_action": True},
                    },
                    indent=2,
                )
            if _is_confirm(message_lower):
                args = dict(pending_action.get("args") or {})
            else:
                return json.dumps(
                    {
                        "status": "pending_confirmation",
                        "message": pending_action.get("message") or "Please confirm.",
                    },
                    indent=2,
                )

        supplier_names_raw = args.get("supplier_names") or args.get("supplier_name")
        supplier_ids_raw = args.get("supplier_ids") or args.get("supplier_id")
        supplier_names: list[str] = []
        supplier_ids: list[uuid.UUID] = []
        if isinstance(supplier_names_raw, list):
            supplier_names = [str(n).strip() for n in supplier_names_raw if str(n).strip()]
        elif isinstance(supplier_names_raw, str) and supplier_names_raw.strip():
            supplier_names = _split_csv(supplier_names_raw.strip())

        if isinstance(supplier_ids_raw, list):
            for sid in supplier_ids_raw:
                try:
                    supplier_ids.append(uuid.UUID(str(sid)))
                except ValueError:
                    return "Error: Invalid supplier_id format in supplier_ids."
        elif isinstance(supplier_ids_raw, str) and supplier_ids_raw.strip():
            try:
                supplier_ids.append(uuid.UUID(supplier_ids_raw.strip()))
            except ValueError:
                return "Error: Invalid supplier_id format."

        if not supplier_names and not supplier_ids:
            return "Error: supplier_names or supplier_ids is required."

        all_outlets = bool(args.get("all_outlets")) if "all_outlets" in args else False
        restaurant_ids_raw = args.get("restaurant_ids") or args.get("restaurant_id")
        restaurant_names_raw = args.get("restaurant_names") or args.get("restaurant_name")
        restaurant_ids: list[uuid.UUID] = []
        restaurant_names: list[str] = []

        if isinstance(restaurant_ids_raw, list):
            for rid in restaurant_ids_raw:
                try:
                    restaurant_ids.append(uuid.UUID(str(rid)))
                except ValueError:
                    return "Error: Invalid restaurant_id format in restaurant_ids."
        elif isinstance(restaurant_ids_raw, str) and restaurant_ids_raw.strip():
            try:
                restaurant_ids.append(uuid.UUID(restaurant_ids_raw.strip()))
            except ValueError:
                return "Error: Invalid restaurant_id format."

        if isinstance(restaurant_names_raw, list):
            restaurant_names = [str(n).strip() for n in restaurant_names_raw if str(n).strip()]
        elif isinstance(restaurant_names_raw, str) and restaurant_names_raw.strip():
            restaurant_names = _split_csv(restaurant_names_raw.strip())

        if not all_outlets and not restaurant_ids and not restaurant_names:
            return "Error: restaurant_ids/restaurant_names is required (or set all_outlets=true)."

        accessible_ids: set[uuid.UUID] = set()
        for rid in (restaurant_roles or {}).keys():
            try:
                accessible_ids.add(uuid.UUID(str(rid)))
            except ValueError:
                continue

        if all_outlets:
            restaurant_ids.extend(list(accessible_ids))

        if restaurant_names:
            restaurants = db.scalars(select(Restaurant).where(Restaurant.id.in_(accessible_ids))).all()
            by_id = {r.id: r for r in restaurants}
            name_index: list[tuple[str, uuid.UUID]] = [
                (r.name.lower(), r.id) for r in restaurants if r.name
            ]
            for query in restaurant_names:
                q = query.lower()
                matches = [rid for name, rid in name_index if q in name or name in q]
                if not matches:
                    return f"Error: No outlet found matching '{query}'."
                if len(set(matches)) > 1:
                    candidates = sorted({by_id[mid].name for mid in matches if mid in by_id})
                    return (
                        f"Error: Multiple outlets match '{query}': "
                        + ", ".join(candidates[:8])
                        + ("" if len(candidates) <= 8 else " ...")
                    )
                restaurant_ids.append(matches[0])

        seen_restaurants: set[uuid.UUID] = set()
        target_outlets: list[uuid.UUID] = []
        for rid in restaurant_ids:
            if rid in seen_restaurants:
                continue
            seen_restaurants.add(rid)
            target_outlets.append(rid)
        if not target_outlets:
            return "Error: No outlets selected."

        for rid in target_outlets:
            if rid not in accessible_ids and not has_restaurant_access(db, user_id, rid):
                return "Error: You don't have access to one of the selected outlets."

        outlet_rows = db.scalars(select(Restaurant).where(Restaurant.id.in_(target_outlets))).all()
        outlet_by_id = {r.id: r for r in outlet_rows}
        outlet_names: list[str] = [
            (outlet_by_id.get(rid).name if outlet_by_id.get(rid) and outlet_by_id.get(rid).name else str(rid))
            for rid in target_outlets
        ]

        status_value = str(args.get("status_value") or args.get("status") or "active").strip().lower()
        if status_value not in {"active", "inactive"}:
            return "Error: status_value must be 'active' or 'inactive'."

        account_number = args.get("account_number")
        default_currency = args.get("default_currency")
        lead_time_days = args.get("lead_time_days")
        notes = args.get("notes")

        suppliers: list[Suppliers] = []
        for supplier_id in supplier_ids:
            supplier = db.get(Suppliers, supplier_id)
            if not supplier:
                return "Error: Supplier not found."
            suppliers.append(supplier)

        for name in supplier_names:
            normalized = normalize_supplier_name(name)
            matches = db.scalars(
                select(Suppliers).where(
                    (Suppliers.user_id == user_id) | (Suppliers.user_id.is_(None)),
                    Suppliers.name_normalized == normalized,
                    Suppliers.is_active == True,
                )
            ).all()
            if not matches:
                supplier = Suppliers(
                    user_id=user_id,
                    name=name,
                    name_normalized=normalized,
                    is_active=True,
                )
                db.add(supplier)
                db.flush()
                suppliers.append(supplier)
            elif len(matches) > 1:
                return f"Error: Multiple suppliers match '{name}'. Please be more specific."
            else:
                suppliers.append(matches[0])

        # De-duplicate suppliers while preserving order
        seen_suppliers: set[uuid.UUID] = set()
        target_suppliers: list[Suppliers] = []
        for supplier in suppliers:
            if supplier.id in seen_suppliers:
                continue
            seen_suppliers.add(supplier.id)
            target_suppliers.append(supplier)
        if not target_suppliers:
            return "Error: No suppliers selected."

        supplier_display = ", ".join([f"'{s.name}'" for s in target_suppliers])
        total_links = len(target_outlets) * len(target_suppliers)
        if not pending_action and total_links > 1:
            msg = responses.SUPPLIER_LINK_MULTI_SUPPLIERS_CONFIRMATION.format(
                suppliers=supplier_display, outlets=", ".join(outlet_names)
            )
            return json.dumps(
                {
                    "status": "needs_confirmation",
                    "message": msg,
                    "context_update": {
                        "pending_action": {
                            "type": "link_suppliers",
                            "args": {
                                "supplier_ids": [str(s.id) for s in target_suppliers],
                                "restaurant_ids": [str(rid) for rid in target_outlets],
                                "status_value": status_value,
                                "account_number": account_number,
                                "default_currency": default_currency,
                                "lead_time_days": lead_time_days,
                                "notes": notes,
                            },
                            "message": msg,
                        }
                    },
                },
                indent=2,
            )

        created_links = 0
        updated_links = 0
        results: list[dict[str, Any]] = []
        for supplier in target_suppliers:
            if not supplier.is_active:
                return f"Error: Supplier '{supplier.name}' is inactive."
            if supplier.user_id is None:
                supplier.user_id = user_id
            elif supplier.user_id != user_id:
                return f"Error: Supplier '{supplier.name}' does not belong to this user."

            for rid in target_outlets:
                link = db.scalar(
                    select(RestaurantSuppliers).where(
                        RestaurantSuppliers.restaurant_id == rid,
                        RestaurantSuppliers.supplier_id == supplier.id,
                    )
                )
                created = False
                if not link:
                    link = RestaurantSuppliers(
                        restaurant_id=rid,
                        supplier_id=supplier.id,
                        status=status_value,
                        account_number=account_number,
                        default_currency=default_currency,
                        lead_time_days=int(lead_time_days) if lead_time_days is not None else None,
                        notes=notes,
                    )
                    db.add(link)
                    created = True
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

                restaurant = outlet_by_id.get(rid) or db.get(Restaurant, rid)
                results.append(
                    {
                        "supplier_name": supplier.name,
                        "restaurant_name": restaurant.name if restaurant else str(rid),
                        "action": "linked" if created else "updated",
                        "status": link.status,
                    }
                )
                if created:
                    created_links += 1
                else:
                    updated_links += 1

        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.exception("link_suppliers_failed")
            return f"Error linking suppliers: {str(e)}"

        return json.dumps(
            {
                "status": "ok",
                "suppliers": [s.name for s in target_suppliers],
                "outlets": outlet_names,
                "outlets_count": len(target_outlets),
                "suppliers_count": len(target_suppliers),
                "created_links": created_links,
                "updated_links": updated_links,
                "results": results,
                "context_update": {
                    "clear_pending_action": True,
                    "active_supplier_id": str(target_suppliers[0].id) if target_suppliers else None,
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
        "link_suppliers": Tool(
            name="link_suppliers",
            description="Link supplier(s) to outlet(s). Supports 1→1, 1→many, many→many, many→1, and all outlets.",
            parameters={
                "type": "object",
                "properties": {
                    "supplier_id": {"type": "string", "description": "Single supplier UUID (optional)."},
                    "supplier_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of supplier UUIDs (optional).",
                    },
                    "supplier_name": {"type": "string", "description": "Single supplier name (optional)."},
                    "supplier_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of supplier names (optional).",
                    },
                    "restaurant_id": {"type": "string", "description": "Single outlet UUID (optional)."},
                    "restaurant_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of outlet UUIDs (optional).",
                    },
                    "restaurant_name": {"type": "string", "description": "Single outlet name (optional)."},
                    "restaurant_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of outlet names (optional).",
                    },
                    "all_outlets": {
                        "type": "boolean",
                        "description": "If true, links to all outlets you can access.",
                    },
                    "status_value": {
                        "type": "string",
                        "description": "Link status: active or inactive (optional).",
                    },
                    "account_number": {
                        "type": "string",
                        "description": "Account number to set on the link(s) (optional).",
                    },
                    "default_currency": {
                        "type": "string",
                        "description": "Default currency to set on the link(s) (optional).",
                    },
                    "lead_time_days": {
                        "type": "integer",
                        "description": "Lead time in days to set on the link(s) (optional).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Notes to set on the link(s) (optional).",
                    },
                },
                "additionalProperties": False,
            },
            handler=link_suppliers,
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
