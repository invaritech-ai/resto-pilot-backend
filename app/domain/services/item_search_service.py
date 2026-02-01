from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.inventory_batches import InventoryBatches
from app.db.models.products import Products
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.supplier_item_products import SupplierItemProducts
from app.db.models.supplier_items import SupplierItems
from app.db.models.supplier_prices import SupplierPrices
from app.db.models.suppliers import Suppliers, normalize_supplier_name


_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


@dataclass(frozen=True)
class ItemSearchTokenOverrides:
    outlet_hint: str | None = None
    supplier_hint: str | None = None
    status: str | None = None  # "active" | "inactive"
    limit: int | None = None
    semantic_text: str = ""


@dataclass(frozen=True)
class ItemSearchRow:
    restaurant_id: uuid.UUID
    restaurant_name: str
    supplier_item_id: uuid.UUID
    supplier_item_name: str
    supplier_id: uuid.UUID
    supplier_name: str
    product_id: uuid.UUID | None
    product_name: str | None
    min_price: float | None = None
    currency: str | None = None


@dataclass(frozen=True)
class ItemSearchPage:
    results: list[ItemSearchRow]
    has_more: bool


def _strip_uuid(text: str) -> str:
    return _UUID_RE.sub("[redacted]", text)


def parse_item_search_tokens(message_text: str) -> ItemSearchTokenOverrides:
    """Parse deterministic key:value tokens and remove them from the semantic text."""
    raw = (message_text or "").strip()
    if not raw:
        return ItemSearchTokenOverrides(semantic_text="")

    tokens = raw.split()
    semantic_parts: list[str] = []
    outlet_hint: str | None = None
    supplier_hint: str | None = None
    status: str | None = None
    limit: int | None = None

    for token in tokens:
        lower = token.lower()
        if lower.startswith("outlet:"):
            value = token[len("outlet:") :].strip()
            outlet_hint = value or outlet_hint
            continue
        if lower.startswith("supplier:"):
            value = token[len("supplier:") :].strip()
            supplier_hint = value or supplier_hint
            continue
        if lower.startswith("status:"):
            value = token[len("status:") :].strip().lower()
            if value in {"active", "inactive"}:
                status = value
            continue
        if lower.startswith("limit:"):
            value = token[len("limit:") :].strip()
            try:
                n = int(value)
            except ValueError:
                n = 0
            if n:
                limit = max(1, min(10, n))
            continue
        semantic_parts.append(token)

    return ItemSearchTokenOverrides(
        outlet_hint=outlet_hint,
        supplier_hint=supplier_hint,
        status=status,
        limit=limit,
        semantic_text=" ".join(semantic_parts).strip(),
    )


def extract_nlp_hints(message_text: str) -> tuple[str | None, str | None]:
    """Extract best-effort outlet/supplier hints from 'for X' / 'from Y' patterns."""
    text = (message_text or "").strip()
    if not text:
        return None, None

    text_clean = re.sub(r"\s+", " ", text)
    lower = text_clean.lower()

    def _extract_after(keyword: str) -> str | None:
        if f" {keyword} " not in f" {lower} ":
            return None
        m = re.search(rf"\b{re.escape(keyword)}\b\s+(.+)$", text_clean, flags=re.IGNORECASE)
        if not m:
            return None
        value = m.group(1).strip()
        # Stop at other common separators / clauses
        lower_value = value.lower()
        for stop in (" from ", " for ", " with ", " status:", " limit:", " outlet:", " supplier:"):
            idx = lower_value.find(stop)
            if idx > 0:
                value = value[:idx].strip()
                lower_value = value.lower()
        value = value.strip(".,!?")
        if 2 <= len(value) <= 50:
            return value
        return None

    outlet = _extract_after("for") or _extract_after("at") or _extract_after("in")
    supplier = _extract_after("from")
    return outlet, supplier


def user_restaurants(db: Session, *, user_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    rows = db.execute(
        select(Restaurant.id, Restaurant.name)
        .join(RestaurantUser, RestaurantUser.restaurant_id == Restaurant.id)
        .where(
            RestaurantUser.user_id == user_id,
            RestaurantUser.status != "removed",
        )
        .order_by(Restaurant.name.asc())
    ).all()
    return [(rid, name) for rid, name in rows]


def resolve_restaurant_id(
    *,
    db: Session,
    user_id: uuid.UUID,
    hint: str | None,
    active_restaurant_id: str | None,
) -> tuple[uuid.UUID | None, list[tuple[uuid.UUID, str]]]:
    restaurants = user_restaurants(db, user_id=user_id)
    if not restaurants:
        return None, restaurants

    if hint:
        hint_str = hint.strip()
        if hint_str.isdigit():
            idx = int(hint_str)
            if 1 <= idx <= len(restaurants):
                return restaurants[idx - 1][0], restaurants
        hint_lower = hint.strip().lower()
        matches = [rid for rid, name in restaurants if hint_lower in name.lower() or name.lower() in hint_lower]
        if len(matches) == 1:
            return matches[0], restaurants

    if isinstance(active_restaurant_id, str) and active_restaurant_id.strip():
        try:
            rid = uuid.UUID(active_restaurant_id.strip())
            if any(rid == r[0] for r in restaurants):
                return rid, restaurants
        except ValueError:
            pass

    if len(restaurants) == 1:
        return restaurants[0][0], restaurants

    return None, restaurants


def resolve_supplier_id_for_restaurant(
    *,
    db: Session,
    restaurant_id: uuid.UUID,
    supplier_hint: str | None,
) -> uuid.UUID | None:
    if not supplier_hint:
        return None
    hint_norm = normalize_supplier_name(supplier_hint)
    if not hint_norm:
        return None

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
    matches = []
    for sid, name in rows:
        name_norm = normalize_supplier_name(name)
        if hint_norm in name_norm or name_norm in hint_norm:
            matches.append(sid)
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_supplier_id_across_restaurants(
    *,
    db: Session,
    restaurant_ids: list[uuid.UUID],
    supplier_hint: str | None,
) -> uuid.UUID | None:
    if not supplier_hint:
        return None
    hint_norm = normalize_supplier_name(supplier_hint)
    if not hint_norm:
        return None
    if not restaurant_ids:
        return None

    rows = db.execute(
        select(Suppliers.id, Suppliers.name)
        .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
        .where(
            RestaurantSuppliers.restaurant_id.in_(restaurant_ids),
            RestaurantSuppliers.status == "active",
            Suppliers.is_active == True,
        )
        .order_by(Suppliers.name.asc())
    ).all()
    matches: set[uuid.UUID] = set()
    for sid, name in rows:
        name_norm = normalize_supplier_name(name)
        if hint_norm in name_norm or name_norm in hint_norm:
            matches.add(sid)
    return next(iter(matches)) if len(matches) == 1 else None


def _search_supplier_items_for_restaurant(
    *,
    db: Session,
    restaurant_id: uuid.UUID,
    restaurant_name: str,
    queries: list[str],
    supplier_id: uuid.UUID | None,
    status: str,
    limit: int,
    offset: int,
    mode: str,
) -> ItemSearchPage:
    q_variants = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:3]
    if not q_variants:
        return ItemSearchPage(results=[], has_more=False)

    fetch_cap = min(60, max(limit + offset + 1, 10))
    now = dt.datetime.now(dt.UTC)

    merged: dict[uuid.UUID, ItemSearchRow] = {}

    for variant_rank, q in enumerate(q_variants):
        pattern = f"%{q}%"

        conditions = [
            RestaurantSuppliers.restaurant_id == restaurant_id,
            RestaurantSuppliers.status == "active",
            Suppliers.is_active == True,
        ]
        if supplier_id is not None:
            conditions.append(Suppliers.id == supplier_id)

        if status == "active":
            conditions.append(SupplierItems.status == "active")
        elif status == "inactive":
            conditions.append(SupplierItems.status != "active")

        text_match = or_(
            SupplierItems.supplier_name_raw.ilike(pattern),
            SupplierItems.supplier_sku.ilike(pattern),
            Products.name_en.ilike(pattern),
            Products.name_local.ilike(pattern),
        )

        stmt = (
            select(
                SupplierItems.id,
                SupplierItems.supplier_name_raw,
                Suppliers.id.label("supplier_id"),
                Suppliers.name.label("supplier_name"),
                SupplierItemProducts.product_id,
                Products.name_en.label("product_name"),
                func.lower(SupplierItems.supplier_name_raw).label("name_lower"),
            )
            .join(Suppliers, Suppliers.id == SupplierItems.supplier_id)
            .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
            .join(
                SupplierItemProducts,
                and_(
                    SupplierItemProducts.supplier_item_id == SupplierItems.id,
                    SupplierItemProducts.restaurant_id == restaurant_id,
                ),
                isouter=True,
            )
            .join(Products, Products.id == SupplierItemProducts.product_id, isouter=True)
            .where(*conditions)
            .where(text_match)
            .order_by(func.length(SupplierItems.supplier_name_raw).asc(), Suppliers.name.asc())
            .limit(fetch_cap)
        )

        rows = db.execute(stmt).all()
        for sid, name_raw, sup_id, sup_name, product_id, product_name, _name_lower in rows:
            if sid in merged:
                continue
            label = name_raw
            product_name_s = product_name if isinstance(product_name, str) and product_name.strip() else None
            row = ItemSearchRow(
                restaurant_id=restaurant_id,
                restaurant_name=restaurant_name,
                supplier_item_id=sid,
                supplier_item_name=label,
                supplier_id=sup_id,
                supplier_name=sup_name,
                product_id=product_id,
                product_name=product_name_s,
            )
            merged[sid] = row

        if len(merged) >= fetch_cap:
            break

    results = list(merged.values())

    if mode == "order" and results:
        item_ids = [r.supplier_item_id for r in results]
        price_rows = db.execute(
            select(
                SupplierPrices.supplier_item_id,
                SupplierPrices.price,
                SupplierPrices.currency,
            )
            .where(
                SupplierPrices.supplier_item_id.in_(item_ids),
                or_(SupplierPrices.valid_to.is_(None), SupplierPrices.valid_to >= now),
            )
        ).all()
        min_map: dict[uuid.UUID, tuple[float, str]] = {}
        for item_id, price, currency in price_rows:
            try:
                p = float(price)
            except (TypeError, ValueError):
                continue
            cur = str(currency) if currency else None
            if not cur:
                continue
            existing = min_map.get(item_id)
            if existing is None or p < existing[0]:
                min_map[item_id] = (p, cur)

        enriched: list[ItemSearchRow] = []
        for r in results:
            mp = min_map.get(r.supplier_item_id)
            if mp:
                enriched.append(
                    ItemSearchRow(
                        restaurant_id=r.restaurant_id,
                        restaurant_name=r.restaurant_name,
                        supplier_item_id=r.supplier_item_id,
                        supplier_item_name=r.supplier_item_name,
                        supplier_id=r.supplier_id,
                        supplier_name=r.supplier_name,
                        product_id=r.product_id,
                        product_name=r.product_name,
                        min_price=mp[0],
                        currency=mp[1],
                    )
                )
            else:
                enriched.append(r)

        results = enriched
        results.sort(
            key=lambda r: (
                1 if r.min_price is None else 0,
                float(r.min_price) if r.min_price is not None else 0.0,
                r.supplier_item_name.lower(),
            )
        )
    else:
        results.sort(key=lambda r: (r.supplier_item_name.lower(), r.supplier_name.lower()))

    page = results[offset : offset + limit]
    has_more = len(results) > (offset + limit)
    return ItemSearchPage(results=page, has_more=has_more)


def search_supplier_items(
    *,
    db: Session,
    restaurant_id: uuid.UUID,
    queries: list[str],
    supplier_id: uuid.UUID | None,
    status: str,
    limit: int,
    offset: int,
    mode: str,
) -> ItemSearchPage:
    restaurant = db.get(Restaurant, restaurant_id)
    restaurant_name = restaurant.name if restaurant else "Outlet"
    return _search_supplier_items_for_restaurant(
        db=db,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_name,
        queries=queries,
        supplier_id=supplier_id,
        status=status,
        limit=limit,
        offset=offset,
        mode=mode,
    )


def search_supplier_items_across_restaurants(
    *,
    db: Session,
    restaurant_ids: list[uuid.UUID],
    queries: list[str],
    supplier_id: uuid.UUID | None,
    status: str,
    limit: int,
    offset: int,
    mode: str,
) -> ItemSearchPage:
    if not restaurant_ids:
        return ItemSearchPage(results=[], has_more=False)

    # Fetch names once
    name_rows = db.execute(
        select(Restaurant.id, Restaurant.name).where(Restaurant.id.in_(restaurant_ids))
    ).all()
    names: dict[uuid.UUID, str] = {rid: name for rid, name in name_rows}

    # Over-fetch a bit per outlet, merge, then paginate globally.
    per_outlet_offset = 0
    per_outlet_limit = min(40, max(limit + offset + 1, 10))

    merged: dict[tuple[uuid.UUID, uuid.UUID], ItemSearchRow] = {}
    has_more_any = False

    for rid in restaurant_ids:
        page = _search_supplier_items_for_restaurant(
            db=db,
            restaurant_id=rid,
            restaurant_name=names.get(rid, "Outlet"),
            queries=queries,
            supplier_id=supplier_id,
            status=status,
            limit=per_outlet_limit,
            offset=per_outlet_offset,
            mode=mode,
        )
        for r in page.results:
            merged[(r.restaurant_id, r.supplier_item_id)] = r
        has_more_any = has_more_any or page.has_more

        if len(merged) >= min(200, offset + limit + 60):
            break

    results = list(merged.values())
    if mode == "order":
        results.sort(
            key=lambda r: (
                1 if r.min_price is None else 0,
                float(r.min_price) if r.min_price is not None else 0.0,
                r.supplier_item_name.lower(),
                r.restaurant_name.lower(),
            )
        )
    else:
        results.sort(
            key=lambda r: (
                r.supplier_item_name.lower(),
                r.supplier_name.lower(),
                r.restaurant_name.lower(),
            )
        )

    page = results[offset : offset + limit]
    has_more = len(results) > (offset + limit) or has_more_any
    return ItemSearchPage(results=page, has_more=has_more)


def load_item_details(
    *,
    db: Session,
    restaurant_id: uuid.UUID,
    supplier_item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, object] | None:
    has_access = db.scalar(
        select(RestaurantUser.id).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == user_id,
            RestaurantUser.status != "removed",
        )
    )
    if not has_access:
        return None

    row = db.execute(
        select(
            SupplierItems,
            Suppliers,
            RestaurantSuppliers,
            SupplierItemProducts,
            Products,
        )
        .join(Suppliers, Suppliers.id == SupplierItems.supplier_id)
        .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
        .where(
            SupplierItems.id == supplier_item_id,
            RestaurantSuppliers.restaurant_id == restaurant_id,
            RestaurantSuppliers.status == "active",
        )
        .join(
            SupplierItemProducts,
            and_(
                SupplierItemProducts.supplier_item_id == SupplierItems.id,
                SupplierItemProducts.restaurant_id == restaurant_id,
            ),
            isouter=True,
        )
        .join(Products, Products.id == SupplierItemProducts.product_id, isouter=True)
        .limit(1)
    ).first()
    if not row:
        return None

    supplier_item, supplier, link, mapping, product = row

    product_id = mapping.product_id if mapping else None
    product_name = product.name_en if product else None

    inventory: dict[str, object] | None = None
    if product_id:
        inv_rows = db.execute(
            select(
                InventoryBatches.unit,
                func.count(InventoryBatches.id),
                func.sum(InventoryBatches.quantity),
            )
            .where(
                InventoryBatches.restaurant_id == restaurant_id,
                InventoryBatches.product_id == product_id,
                InventoryBatches.status == "available",
            )
            .group_by(InventoryBatches.unit)
        ).all()
        if inv_rows:
            inventory = {
                "units": [
                    {
                        "unit": unit,
                        "batches": int(batch_count or 0),
                        "quantity": float(qty_sum or 0.0),
                    }
                    for unit, batch_count, qty_sum in inv_rows
                ]
            }

    now = dt.datetime.now(dt.UTC)
    prices = db.execute(
        select(SupplierPrices)
        .where(
            SupplierPrices.supplier_item_id == supplier_item_id,
            or_(SupplierPrices.valid_to.is_(None), SupplierPrices.valid_to >= now),
        )
        .order_by(SupplierPrices.valid_from.desc())
        .limit(10)
    ).scalars().all()

    price_options = []
    for p in prices:
        try:
            price_value = float(p.price)
        except (TypeError, ValueError):
            continue
        price_options.append(
            {
                "price": price_value,
                "currency": p.currency,
                "price_type": p.price_type,
                "min_qty": float(p.min_qty) if p.min_qty is not None else None,
                "valid_from": p.valid_from.isoformat() if p.valid_from else None,
                "valid_to": p.valid_to.isoformat() if p.valid_to else None,
            }
        )

    return {
        "supplier_item_name": supplier_item.supplier_name_raw,
        "supplier_sku": supplier_item.supplier_sku,
        "pack_size_text": supplier_item.pack_size_text,
        "unit_basis": supplier_item.unit_basis,
        "supplier_item_status": supplier_item.status,
        "supplier_id": supplier.id,
        "supplier_name": supplier.name,
        "contact_name": supplier.contact_name,
        "contact_email": supplier.contact_email,
        "contact_phone": supplier.contact_phone,
        "account_number": link.account_number if link else None,
        "notes": link.notes if link else None,
        "product_id": product_id,
        "product_name": product_name,
        "inventory": inventory,
        "prices": price_options,
    }


def load_supplier_details(
    *,
    db: Session,
    restaurant_id: uuid.UUID,
    supplier_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, object] | None:
    has_access = db.scalar(
        select(RestaurantUser.id).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == user_id,
            RestaurantUser.status != "removed",
        )
    )
    if not has_access:
        return None

    row = db.execute(
        select(Suppliers, RestaurantSuppliers)
        .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
        .where(
            RestaurantSuppliers.restaurant_id == restaurant_id,
            Suppliers.id == supplier_id,
            RestaurantSuppliers.status == "active",
            Suppliers.is_active == True,
        )
        .limit(1)
    ).first()
    if not row:
        return None
    supplier, link = row
    return {
        "supplier_name": supplier.name,
        "contact_name": supplier.contact_name,
        "contact_email": supplier.contact_email,
        "contact_phone": supplier.contact_phone,
        "currency": link.default_currency or supplier.currency,
        "lead_time_days": link.lead_time_days or supplier.lead_time_days,
        "account_number": link.account_number,
        "notes": link.notes,
    }


def format_item_search_list(*, mode: str, page: ItemSearchPage, include_outlet: bool | None = None) -> str:
    rows = page.results
    title = "🛒 Order options" if mode == "order" else "🔎 Items"
    if not rows:
        return f"{title}\n\nNo results. Try a different search."

    show_outlet = include_outlet if isinstance(include_outlet, bool) else len({r.restaurant_id for r in rows}) > 1

    lines = [f"{title} ({len(rows)} shown)", ""]
    for idx, r in enumerate(rows, 1):
        if mode == "order":
            if r.min_price is None or not r.currency:
                price_part = "Price unavailable"
            else:
                price_part = f"{r.min_price:g} {r.currency}"
            outlet_part = f" — {r.restaurant_name}" if show_outlet else ""
            lines.append(
                f"{idx}. {r.supplier_item_name} — {price_part} — {r.supplier_name}{outlet_part}"
            )
        else:
            suffix = f" — {r.supplier_name}"
            if r.product_name:
                suffix = f" — {r.product_name} — {r.supplier_name}"
            outlet_part = f" — {r.restaurant_name}" if show_outlet else ""
            lines.append(f"{idx}. {r.supplier_item_name}{suffix}{outlet_part}")

    lines.append("")
    if page.has_more:
        lines.append('Reply "more" to see the next results.')
    open_index = 2 if len(rows) >= 2 else 1
    lines.append(f'Reply "open {open_index}" to see item #{open_index}.')
    if mode == "order":
        lines.append(f'Reply "supplier {open_index}" to see supplier details for #{open_index}.')
    lines.append('Reply "search item <new query>" to start over.')
    return _strip_uuid("\n".join(lines).strip())


def format_item_details(*, details: dict[str, object]) -> str:
    name = str(details.get("supplier_item_name") or "").strip() or "Item"
    supplier_name = str(details.get("supplier_name") or "").strip() or "Unknown supplier"
    lines = [f"📦 {name}", f"Supplier: {supplier_name}"]

    contact_phone = details.get("contact_phone")
    contact_email = details.get("contact_email")
    if isinstance(contact_phone, str) and contact_phone.strip():
        lines.append(f"Phone: {contact_phone.strip()}")
    if isinstance(contact_email, str) and contact_email.strip():
        lines.append(f"Email: {contact_email.strip()}")

    account_number = details.get("account_number")
    if isinstance(account_number, str) and account_number.strip():
        lines.append(f"Account: {account_number.strip()}")

    sku = details.get("supplier_sku")
    if isinstance(sku, str) and sku.strip():
        lines.append(f"SKU: {sku.strip()}")

    pack = details.get("pack_size_text")
    unit_basis = details.get("unit_basis")
    if isinstance(pack, str) and pack.strip():
        lines.append(f"Pack: {pack.strip()}")
    if isinstance(unit_basis, str) and unit_basis.strip():
        lines.append(f"Unit: {unit_basis.strip()}")

    product_name = details.get("product_name")
    if isinstance(product_name, str) and product_name.strip():
        lines.append(f"Mapped product: {product_name.strip()}")
    else:
        lines.append("Mapped product: Not mapped yet")

    inventory = details.get("inventory")
    if isinstance(inventory, dict):
        units = inventory.get("units")
        if isinstance(units, list) and units:
            inv_parts = []
            for entry in units[:2]:
                if not isinstance(entry, dict):
                    continue
                unit = entry.get("unit")
                qty = entry.get("quantity")
                batches = entry.get("batches")
                if isinstance(unit, str) and isinstance(qty, (int, float)):
                    batch_part = f"{int(batches)} batches" if isinstance(batches, int) else None
                    qty_part = f"{qty:g} {unit}"
                    inv_parts.append(" • ".join([p for p in [batch_part, qty_part] if p]))
            if inv_parts:
                lines.append("Inventory: " + " | ".join(inv_parts))

    prices = details.get("prices")
    if isinstance(prices, list) and prices:
        lines.append("Prices:")
        for p in prices[:6]:
            if not isinstance(p, dict):
                continue
            price = p.get("price")
            currency = p.get("currency")
            price_type = p.get("price_type")
            min_qty = p.get("min_qty")
            if not isinstance(price, (int, float)) or not isinstance(currency, str):
                continue
            parts = [f"{price:g} {currency}"]
            if isinstance(price_type, str) and price_type:
                parts.append(price_type)
            if isinstance(min_qty, (int, float)):
                parts.append(f"min {min_qty:g}")
            lines.append("- " + " • ".join(parts))

    lines.append("")
    lines.append('Reply "more" for more results, or "search item <query>" to search again.')
    return _strip_uuid("\n".join(lines).strip())


def format_supplier_details(*, details: dict[str, object]) -> str:
    supplier_name = str(details.get("supplier_name") or "").strip() or "Supplier"
    lines = [f"🏷️ {supplier_name}"]

    contact_name = details.get("contact_name")
    contact_phone = details.get("contact_phone")
    contact_email = details.get("contact_email")
    if isinstance(contact_name, str) and contact_name.strip():
        lines.append(f"Contact: {contact_name.strip()}")
    if isinstance(contact_phone, str) and contact_phone.strip():
        lines.append(f"Phone: {contact_phone.strip()}")
    if isinstance(contact_email, str) and contact_email.strip():
        lines.append(f"Email: {contact_email.strip()}")

    account_number = details.get("account_number")
    if isinstance(account_number, str) and account_number.strip():
        lines.append(f"Account: {account_number.strip()}")

    currency = details.get("currency")
    if isinstance(currency, str) and currency.strip():
        lines.append(f"Currency: {currency.strip()}")

    lead = details.get("lead_time_days")
    if isinstance(lead, int):
        lines.append(f"Lead time: {lead} days")

    notes = details.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(f"Notes: {notes.strip()}")

    return _strip_uuid("\n".join(lines).strip())
