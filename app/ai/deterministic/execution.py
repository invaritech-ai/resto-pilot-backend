from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.conversation import responses
from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.supplier_items import SupplierItems
from app.db.models.supplier_prices import SupplierPrices
from app.db.models.suppliers import Suppliers
from app.db.models.user import User
from app.domain.services.entity_resolver import Candidate, normalize_name, resolve_name
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService

logger = logging.getLogger(__name__)


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def _is_owner(*, db: Session, user: User, restaurant_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(RestaurantUser.id).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user.id,
                RestaurantUser.role == "owner",
                RestaurantUser.status == "active",
            )
        )
    )


def _has_access(*, db: Session, user: User, restaurant_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(RestaurantUser.id).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user.id,
                RestaurantUser.status == "active",
            )
        )
    )


def _resolve_restaurant_id(
    *,
    db: Session,
    user: User,
    restaurant_query: str | None,
    context: dict[str, Any] | None,
) -> tuple[uuid.UUID | None, str | None, dict[str, Any] | None]:
    if not restaurant_query:
        active = (context or {}).get("active_restaurant_id")
        rid = _uuid_or_none(active if isinstance(active, str) else None)
        if rid is None:
            return (
                None,
                None,
                {
                    "error": "No outlet selected. Specify an outlet name or ask 'which outlets am I part of?'."
                },
            )
        restaurant = db.get(Restaurant, rid)
        return rid, (restaurant.name if restaurant else None), None

    rows = RestaurantService(db).list_for_user(user_id=user.id)
    candidates = [
        Candidate(id=str(r.id), display=r.name, normalized=normalize_name(r.name, kind="restaurant"))
        for r, _m in rows
    ]
    resolved = resolve_name(
        kind="restaurant",
        query=restaurant_query,
        candidates=candidates,
        min_score=0.82,
        min_gap=0.06,
        max_candidates=5,
    )
    if resolved.status == "resolved" and resolved.id:
        rid = _uuid_or_none(resolved.id)
        return rid, resolved.display, None
    if resolved.candidates:
        if resolved.status == "ambiguous":
            return None, None, {
                "error": "Which outlet did you mean?",
                "choices": [c.display for c in resolved.candidates],
            }
        return None, None, {
            "error": "Outlet not found. Did you mean one of these?",
            "choices": [c.display for c in resolved.candidates],
        }
    return None, None, {"error": "Outlet not found."}


def _visible_supplier_candidates(*, db: Session, user: User) -> list[Candidate]:
    supplier_ids_linked = db.scalars(
        select(RestaurantSuppliers.supplier_id)
        .join(
            RestaurantUser,
            and_(
                RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                RestaurantUser.user_id == user.id,
                RestaurantUser.status == "active",
            ),
        )
        .where(RestaurantSuppliers.status == "active")
        .distinct()
    ).all()

    query = select(Suppliers).where(Suppliers.is_active == True)
    if supplier_ids_linked:
        query = query.where(or_(Suppliers.user_id == user.id, Suppliers.id.in_(supplier_ids_linked)))
    else:
        query = query.where(Suppliers.user_id == user.id)

    rows = db.scalars(query.order_by(Suppliers.name.asc())).all()
    return [
        Candidate(
            id=str(s.id),
            display=s.name,
            normalized=normalize_name(s.name, kind="supplier"),
        )
        for s in rows
    ]


def _resolve_supplier_id_visible(
    *,
    db: Session,
    user: User,
    supplier_query: str | None,
    restaurant_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, str | None, dict[str, Any] | None]:
    if not supplier_query:
        return None, None, None

    if restaurant_id is not None:
        rows = db.execute(
            select(Suppliers.id, Suppliers.name)
            .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
            .where(
                RestaurantSuppliers.restaurant_id == restaurant_id,
                RestaurantSuppliers.status == "active",
                Suppliers.is_active == True,
            )
            .order_by(Suppliers.name.asc())
        ).all()
        candidates = [
            Candidate(id=str(sid), display=name, normalized=normalize_name(name, kind="supplier"))
            for sid, name in rows
        ]
    else:
        candidates = _visible_supplier_candidates(db=db, user=user)

    resolved = resolve_name(
        kind="supplier",
        query=supplier_query,
        candidates=candidates,
        min_score=0.82,
        min_gap=0.06,
        max_candidates=5,
    )
    if resolved.status == "resolved" and resolved.id:
        sid = _uuid_or_none(resolved.id)
        return sid, resolved.display, None
    if resolved.candidates:
        if resolved.status == "ambiguous":
            return None, None, {
                "error": "Which supplier did you mean?",
                "choices": [c.display for c in resolved.candidates],
            }
        return None, None, {
            "error": "Supplier not found. Did you mean one of these?",
            "choices": [c.display for c in resolved.candidates],
        }
    return None, None, {"error": "Supplier not found. Upload a supplier price list to add it."}


def execute_deterministic_tool(
    *,
    db: Session,
    user: User,
    tool: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> str:
    """
    Execute a deterministic tool by name.

    Tool outputs should be JSON strings. The Presenter is responsible for rendering.
    """
    try:
        if tool == "restaurants_list":
            rows = RestaurantService(db).list_for_user(user_id=user.id)
            payload = [
                {"name": restaurant.name, "your_role": membership.role}
                for restaurant, membership in rows
            ]
            return json.dumps({"restaurants": payload}, indent=2)

        if tool == "restaurants_create":
            name_raw = args.get("name")
            name = name_raw.strip() if isinstance(name_raw, str) else ""
            if not name:
                return json.dumps({"error": "name is required"}, indent=2)
            restaurant = RestaurantService(db).create_restaurant(owner_user_id=user.id, name=name)
            return json.dumps(
                {
                    "status": "created",
                    "restaurant": {"name": restaurant.name},
                    "context_update": {"active_restaurant_id": str(restaurant.id)},
                },
                indent=2,
            )

        if tool == "restaurants_update":
            new_name_raw = args.get("name")
            new_name = new_name_raw.strip() if isinstance(new_name_raw, str) else ""
            if not new_name:
                return json.dumps({"error": "name is required"}, indent=2)

            rq = args.get("restaurant_query")
            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db,
                user=user,
                restaurant_query=rq.strip() if isinstance(rq, str) else None,
                context=context,
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _is_owner(db=db, user=user, restaurant_id=restaurant_id):
                return json.dumps({"error": "Only owners can update outlet details."}, indent=2)

            restaurant = db.get(Restaurant, restaurant_id)
            if not restaurant:
                return json.dumps({"error": "Outlet not found."}, indent=2)
            restaurant.name = new_name
            db.add(restaurant)
            db.commit()
            return json.dumps(
                {
                    "status": "updated",
                    "previous_name": restaurant_name,
                    "restaurant": {"name": new_name},
                    "context_update": {"active_restaurant_id": str(restaurant_id)},
                },
                indent=2,
            )

        if tool == "staff_list":
            rq = args.get("restaurant_query")
            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db,
                user=user,
                restaurant_query=rq.strip() if isinstance(rq, str) else None,
                context=context,
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _has_access(db=db, user=user, restaurant_id=restaurant_id):
                return json.dumps({"error": "You don't have access to this outlet."}, indent=2)

            members = RestaurantService(db).list_members(restaurant_id=restaurant_id)
            payload = [
                {
                    "name": (u.full_name or "Unknown"),
                    "username": (f"@{u.username}" if u.username else None),
                    "role": m.role,
                    "status": m.status,
                }
                for u, m in members
                if m.status == "active"
            ]
            return json.dumps({"restaurant_name": restaurant_name, "members": payload}, indent=2)

        if tool == "suppliers_list":
            rq = args.get("restaurant_query")
            if isinstance(rq, str) and rq.strip():
                restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                    db=db, user=user, restaurant_query=rq.strip(), context=context
                )
                if err or restaurant_id is None:
                    return json.dumps(err or {"error": "Outlet not found."}, indent=2)
                if not _has_access(db=db, user=user, restaurant_id=restaurant_id):
                    return json.dumps({"error": "You don't have access to this outlet."}, indent=2)

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
                suppliers = [
                    {
                        "name": s.name,
                        "currency": (link.default_currency or s.currency),
                        "language": s.language,
                        "lead_time_days": (link.lead_time_days or s.lead_time_days),
                    }
                    for s, link in rows
                ]
                return json.dumps({"restaurant_name": restaurant_name, "suppliers": suppliers}, indent=2)

            rows = db.execute(
                select(Suppliers.name)
                .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
                .join(
                    RestaurantUser,
                    and_(
                        RestaurantUser.restaurant_id == RestaurantSuppliers.restaurant_id,
                        RestaurantUser.user_id == user.id,
                        RestaurantUser.status == "active",
                    ),
                )
                .where(RestaurantSuppliers.status == "active", Suppliers.is_active == True)
                .distinct()
                .order_by(Suppliers.name.asc())
            ).all()
            return json.dumps({"suppliers": [{"name": name} for (name,) in rows]}, indent=2)

        if tool == "restaurant_suppliers_link":
            rq = args.get("restaurant_query")
            sq = args.get("supplier_query")
            if not isinstance(rq, str) or not rq.strip():
                return json.dumps({"error": "restaurant_query is required"}, indent=2)
            if not isinstance(sq, str) or not sq.strip():
                return json.dumps({"error": "supplier_query is required"}, indent=2)

            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db, user=user, restaurant_query=rq.strip(), context=context
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _has_access(db=db, user=user, restaurant_id=restaurant_id):
                return json.dumps({"error": "You don't have access to this outlet."}, indent=2)

            supplier_id, supplier_name, serr = _resolve_supplier_id_visible(
                db=db, user=user, supplier_query=sq.strip()
            )
            if serr or supplier_id is None:
                return json.dumps(
                    serr or {"error": "Supplier not found. Upload a supplier price list to add it."},
                    indent=2,
                )

            link = db.scalar(
                select(RestaurantSuppliers).where(
                    RestaurantSuppliers.restaurant_id == restaurant_id,
                    RestaurantSuppliers.supplier_id == supplier_id,
                )
            )
            if link is None:
                link = RestaurantSuppliers(
                    restaurant_id=restaurant_id,
                    supplier_id=supplier_id,
                    status="active",
                )
                db.add(link)
            else:
                link.status = "active"
                db.add(link)
            db.commit()
            return json.dumps(
                {"status": "linked", "restaurant_name": restaurant_name, "supplier_name": supplier_name},
                indent=2,
            )

        if tool == "restaurant_suppliers_unlink":
            rq = args.get("restaurant_query")
            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db,
                user=user,
                restaurant_query=rq.strip() if isinstance(rq, str) else None,
                context=context,
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _is_owner(db=db, user=user, restaurant_id=restaurant_id):
                return json.dumps({"error": "Only owners can unlink suppliers."}, indent=2)

            supplier_ref = args.get("supplier_ref")
            supplier_query = args.get("supplier_query")

            supplier_id: uuid.UUID | None = None
            supplier_name: str | None = None
            if isinstance(supplier_ref, int) and supplier_ref > 0:
                rows = db.execute(
                    select(Suppliers.id, Suppliers.name)
                    .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
                    .where(
                        RestaurantSuppliers.restaurant_id == restaurant_id,
                        RestaurantSuppliers.status == "active",
                        Suppliers.is_active == True,
                    )
                    .order_by(Suppliers.name.asc())
                ).all()
                if supplier_ref <= len(rows):
                    supplier_id, supplier_name = rows[supplier_ref - 1]
                else:
                    return json.dumps(
                        {"error": f"Supplier #{supplier_ref} not found for this outlet."},
                        indent=2,
                    )
            else:
                supplier_id, supplier_name, serr = _resolve_supplier_id_visible(
                    db=db,
                    user=user,
                    supplier_query=supplier_query.strip() if isinstance(supplier_query, str) else None,
                    restaurant_id=restaurant_id,
                )
                if serr or supplier_id is None:
                    return json.dumps(serr or {"error": "Supplier not found."}, indent=2)

            link = db.scalar(
                select(RestaurantSuppliers).where(
                    RestaurantSuppliers.restaurant_id == restaurant_id,
                    RestaurantSuppliers.supplier_id == supplier_id,
                )
            )
            if link is None or link.status != "active":
                return json.dumps({"error": "Supplier is not linked to this outlet."}, indent=2)

            link.status = "inactive"
            db.add(link)
            db.commit()
            return json.dumps(
                {"status": "unlinked", "restaurant_name": restaurant_name, "supplier_name": supplier_name},
                indent=2,
            )

        if tool == "invite_codes_create":
            from app.core.config import get_settings

            rq = args.get("restaurant_query")
            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db,
                user=user,
                restaurant_query=rq.strip() if isinstance(rq, str) else None,
                context=context,
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)

            role_raw = args.get("role")
            role = role_raw if isinstance(role_raw, str) and role_raw in {"staff", "owner"} else "staff"
            expires_in_days = args.get("expires_in_days")
            expires_at = None
            if isinstance(expires_in_days, int) and expires_in_days > 0:
                expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=int(expires_in_days))

            try:
                invite = InviteCodeService(db).create_invite_code(
                    restaurant_id=restaurant_id,
                    target_role=role,
                    created_by_user_id=user.id,
                    created_by_is_superuser=False,
                    expires_at=expires_at,
                )
            except PermissionError:
                return json.dumps({"error": "Only owners can create invite codes."}, indent=2)

            settings = get_settings()
            deep_link = InviteCodeService.deep_link(
                bot_username=settings.telegram_bot_username,
                code=invite.code,
            )
            return json.dumps(
                {
                    "status": "created",
                    "restaurant_name": restaurant_name,
                    "code": invite.code,
                    "role": invite.role,
                    "deep_link": deep_link,
                    "expires_at": (invite.expires_at.isoformat() if invite.expires_at else None),
                },
                indent=2,
            )

        if tool == "invite_codes_list":
            from app.core.config import get_settings

            rq = args.get("restaurant_query")
            restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                db=db,
                user=user,
                restaurant_query=rq.strip() if isinstance(rq, str) else None,
                context=context,
            )
            if err or restaurant_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _is_owner(db=db, user=user, restaurant_id=restaurant_id):
                return json.dumps({"error": "Only owners can view invite codes."}, indent=2)

            include_used = bool(args.get("include_used")) if "include_used" in args else False
            include_expired = bool(args.get("include_expired")) if "include_expired" in args else False
            now = dt.datetime.now(dt.UTC)

            invites = db.scalars(
                select(InviteCodes)
                .where(InviteCodes.restaurant_id == restaurant_id)
                .order_by(InviteCodes.created_at.desc())
            ).all()
            settings = get_settings()

            out = []
            for inv in invites:
                if not include_used and inv.used_at is not None:
                    continue
                if not include_expired and inv.expires_at is not None and inv.expires_at <= now:
                    continue
                deep_link = (
                    InviteCodeService.deep_link(
                        bot_username=settings.telegram_bot_username,
                        code=inv.code,
                    )
                    if inv.used_at is None
                    else None
                )
                out.append(
                    {
                        "code": inv.code,
                        "role": inv.role,
                        "deep_link": deep_link,
                        "expires_at": (inv.expires_at.isoformat() if inv.expires_at else None),
                        "used_at": (inv.used_at.isoformat() if inv.used_at else None),
                    }
                )
            return json.dumps({"restaurant_name": restaurant_name, "invites": out}, indent=2)

        if tool == "invite_codes_move":
            code_raw = args.get("code")
            target_query = args.get("restaurant_query")
            code = code_raw.strip().upper() if isinstance(code_raw, str) else ""
            if not code:
                return json.dumps({"error": "code is required"}, indent=2)
            if not isinstance(target_query, str) or not target_query.strip():
                return json.dumps({"error": "restaurant_query is required"}, indent=2)

            invite = db.scalar(select(InviteCodes).where(InviteCodes.code == code))
            if invite is None:
                return json.dumps({"error": "Invite code not found."}, indent=2)
            if invite.used_at is not None:
                return json.dumps({"error": "Invite code has already been used."}, indent=2)
            if invite.expires_at is not None and invite.expires_at <= dt.datetime.now(dt.UTC):
                return json.dumps({"error": "Invite code is expired."}, indent=2)
            if not _is_owner(db=db, user=user, restaurant_id=invite.restaurant_id):
                return json.dumps({"error": "Only owners can move invite codes."}, indent=2)

            target_id, target_name, err = _resolve_restaurant_id(
                db=db, user=user, restaurant_query=target_query.strip(), context=context
            )
            if err or target_id is None:
                return json.dumps(err or {"error": "Outlet not found."}, indent=2)
            if not _is_owner(db=db, user=user, restaurant_id=target_id):
                return json.dumps({"error": "You must be an owner of the target outlet to move invites."}, indent=2)

            invite.restaurant_id = target_id
            db.add(invite)
            db.commit()
            return json.dumps({"status": "moved", "code": code, "restaurant_name": target_name}, indent=2)

        if tool == "supplier_items_search":
            item_query_raw = args.get("item_query")
            item_query = item_query_raw.strip() if isinstance(item_query_raw, str) else ""
            if not item_query:
                return json.dumps({"error": "item_query is required"}, indent=2)

            limit_raw = args.get("limit")
            limit = int(limit_raw) if isinstance(limit_raw, int) and limit_raw > 0 else 10
            limit = min(max(limit, 1), 40)

            cursor_raw = args.get("cursor")
            offset = int(cursor_raw.strip()) if isinstance(cursor_raw, str) and cursor_raw.strip().isdigit() else 0

            restaurant_query = args.get("restaurant_query")
            supplier_query = args.get("supplier_query")

            restaurant_id: uuid.UUID | None = None
            restaurant_name: str | None = None
            if isinstance(restaurant_query, str) and restaurant_query.strip():
                restaurant_id, restaurant_name, err = _resolve_restaurant_id(
                    db=db, user=user, restaurant_query=restaurant_query.strip(), context=context
                )
                if err or restaurant_id is None:
                    return json.dumps(err or {"error": "Outlet not found."}, indent=2)
                if not _has_access(db=db, user=user, restaurant_id=restaurant_id):
                    return json.dumps({"error": "You don't have access to this outlet."}, indent=2)

            supplier_id, supplier_name, serr = _resolve_supplier_id_visible(
                db=db,
                user=user,
                supplier_query=supplier_query.strip() if isinstance(supplier_query, str) else None,
                restaurant_id=restaurant_id,
            )
            if serr and supplier_query:
                return json.dumps(serr, indent=2)

            visible_suppliers = _visible_supplier_candidates(db=db, user=user)
            visible_ids = [_uuid_or_none(c.id) for c in visible_suppliers]
            visible_ids = [v for v in visible_ids if v is not None]
            if not visible_ids:
                return json.dumps({"items": [], "has_more": False}, indent=2)

            now = dt.datetime.now(dt.UTC)
            pattern = f"%{item_query}%"

            conditions = [Suppliers.is_active == True, Suppliers.id.in_(visible_ids)]
            if supplier_id is not None:
                conditions.append(Suppliers.id == supplier_id)

            text_match = or_(
                SupplierItems.supplier_name_raw.ilike(pattern),
                SupplierItems.supplier_sku.ilike(pattern),
            )

            stmt = (
                select(
                    SupplierItems.id,
                    SupplierItems.supplier_name_raw,
                    SupplierItems.supplier_sku,
                    Suppliers.name.label("supplier_name"),
                )
                .join(Suppliers, Suppliers.id == SupplierItems.supplier_id)
                .where(*conditions)
                .where(text_match)
                .order_by(func.length(SupplierItems.supplier_name_raw).asc(), Suppliers.name.asc())
                .offset(offset)
                .limit(limit + 1)
            )
            rows = db.execute(stmt).all()
            has_more = len(rows) > limit
            rows = rows[:limit]

            item_ids = [sid for sid, _n, _sku, _sn in rows]
            price_rows = []
            if item_ids:
                price_rows = db.execute(
                    select(
                        SupplierPrices.supplier_item_id,
                        SupplierPrices.price,
                        SupplierPrices.currency,
                    ).where(
                        SupplierPrices.supplier_item_id.in_(item_ids),
                        or_(SupplierPrices.valid_to.is_(None), SupplierPrices.valid_to >= now),
                    )
                ).all()

            best_price: dict[uuid.UUID, tuple[float, str]] = {}
            for item_id, price, currency in price_rows:
                try:
                    p = float(price)
                except Exception:
                    continue
                curr = str(currency) if isinstance(currency, str) else ""
                prev = best_price.get(item_id)
                if prev is None or p < prev[0]:
                    best_price[item_id] = (p, curr)

            items = []
            for item_id, name_raw, sku, sup_name in rows:
                mp = best_price.get(item_id)
                items.append(
                    {
                        "name": name_raw,
                        "supplier": sup_name,
                        "sku": sku,
                        "min_price": (mp[0] if mp else None),
                        "currency": (mp[1] if mp else None),
                    }
                )

            payload: dict[str, Any] = {"items": items, "has_more": has_more}
            if restaurant_name:
                payload["restaurant_name"] = restaurant_name
            if supplier_name:
                payload["supplier_name"] = supplier_name
            if has_more:
                payload["cursor"] = str(offset + limit)
            return json.dumps(payload, indent=2)

        if tool == "help":
            topic_raw = args.get("topic")
            topic = topic_raw.strip().lower() if isinstance(topic_raw, str) else None
            return json.dumps({"text": responses.help_topic(topic)}, indent=2)

        return json.dumps({"error": f"Tool not implemented: {tool}"}, indent=2)
    except Exception as exc:
        logger.exception("deterministic_tool_failed", extra={"tool": tool, "error": str(exc)})
        return json.dumps({"error": "Tool execution failed"}, indent=2)
