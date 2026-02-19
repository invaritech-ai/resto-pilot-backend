"""Priority 3: Slash command handler.

Commands:
    /profile           — Your profile
    /team              — Staff & invites
    /list suppliers    — list this restaurant's linked suppliers
    /add supplier      — create a new supplier (search global + create if not found)
    /link supplier     — link an existing global supplier to restaurant
    /outlets           — Your restaurant
    /products          — Supplier product catalog
    /prices <name>     — Prices from a supplier
    /inventory         — Stock levels
    /balance           — Stock summary
    /uploads           — list pending staging records
    /help              — show available commands
"""

from __future__ import annotations

import math
import re
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.suppliers import Supplier
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.inventory_service import InventoryService
from app.services.money import format_price
from app.services.restaurant_service import RestaurantService
from app.services.supplier_service import AlreadyLinkedError, SupplierService
from app.telegram.bot_api import (
    edit_message_reply_markup,
    send_document,
    send_message,
    send_message_with_keyboard,
    send_photo,
)
from app.telegram.keyboards import pagination_keyboard, quick_adj_rows
from app.telegram.renderer import (
    format_with_emoji,
    format_supplier_name,
    format_price_monospace,
    format_footer,
)


HELP_TEXT = """Available commands:

📦 Inventory
/inventory         — Stock levels (with [+]/[-] quick adjust buttons)
/balance           — Stock summary (zero & negative alerts)
/chart             — Visual stock levels chart
/history <item>    — Recent transactions for an item
/export            — Download inventory as CSV

🛒 Suppliers & Prices
/list suppliers    — Show your linked suppliers
/add supplier <name> — Add a new supplier
/products          — Supplier product catalog
/prices <name>     — Prices from a supplier

📂 Uploads
/uploads           — Pending upload reviews

👤 Account
/profile           — Your profile
/team              — Staff & invites

🔍 Search
/search <query>    — Search inventory, products & suppliers at once

💬 Free text shortcuts
"used 1kg onion"   — Record stock usage (with confirm)
"2kg chicken left" — Set stock balance (with confirm)
Or just ask: "where can I buy truffle?" / "how much onion do I have?"

/help              — Show this message"""


def _require_active_restaurant(
    user: User,
    ctx_svc: ContextService,
    db: Session,
    settings: Settings,
) -> uuid.UUID | None:
    """Return active_restaurant_id or None if not set.

    If user has no active restaurant but has exactly one restaurant,
    auto-set it. Otherwise return None (caller shows setup prompt).

    Membership is always verified against the database before returning an ID
    so that a corrupted or tampered context cannot grant access to a restaurant
    the user is not a member of.
    """
    active_id = ctx_svc.get_active_restaurant_id(user)
    if active_id:
        # Tenant-isolation guard: verify the user is still an active member.
        if RestaurantService(db).user_membership_exists(
            restaurant_id=active_id, user_id=user.id
        ):
            return active_id
        # Stale / corrupted context — clear the invalid value and fall through.
        ctx_svc.set_fields(user, active_restaurant_id=None)
        db.commit()

    # Check if user has exactly one restaurant
    restaurants = RestaurantService(db).list_for_user(user_id=user.id)
    if len(restaurants) == 1:
        restaurant, _ = restaurants[0]
        ctx_svc.set_active_restaurant(user, restaurant.id)
        return restaurant.id

    # Need to prompt /switch
    return None


def _parse_command(text: str) -> tuple[str, str]:
    """Parse command text into (command, args).

    Examples:
        "/list suppliers" → ("list suppliers", "")
        "/add supplier ABC" → ("add supplier", "ABC")
        "/link supplier   XYZ Corp" → ("link supplier", "XYZ Corp")
        "/prices ABC Wholesalers" → ("prices", "ABC Wholesalers")
    """
    text = text.strip()
    # Match command patterns — new commands first, then existing
    patterns = [
        (r"^/profile\b", "profile"),
        (r"^/team\b", "team"),
        (r"^/outlets\b", "outlets"),
        (r"^/products\b", "products"),
        (r"^/prices\b", "prices"),
        (r"^/inventory\b", "inventory"),
        (r"^/balance\b", "balance"),
        (r"^/list suppliers\b", "list suppliers"),
        (r"^/add supplier\b", "add supplier"),
        (r"^/link supplier\b", "link supplier"),
        (r"^/uploads\b", "uploads"),
        (r"^/history\b", "history"),
        (r"^/export\b", "export"),
        (r"^/chart\b", "chart"),
        (r"^/search\b", "search"),
        (r"^/help\b", "help"),
        (r"^/start\b", "start"),
    ]

    for pattern, cmd_name in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            args = text[match.end():].strip()
            return cmd_name, args

    # Unknown command
    return text.split()[0].lower() if text else "", ""


_LIST_PAGE_SIZE = 10


def _pagination_markup(
    *,
    list_type: str,
    current_page: int,
    total_pages: int,
) -> dict | None:
    """Return inline pagination keyboard or None when not needed."""
    if total_pages <= 1:
        return None
    markup = pagination_keyboard(
        list_type=list_type,
        current_page=current_page,
        has_next=current_page < total_pages - 1,
        has_prev=current_page > 0,
    )
    if not markup.get("inline_keyboard"):
        return None
    return markup


def _send_with_optional_keyboard(
    *,
    chat_id: int,
    text: str,
    reply_markup: dict | None,
    settings: Settings,
) -> int | None:
    if reply_markup and reply_markup.get("inline_keyboard"):
        return send_message_with_keyboard(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            settings=settings,
        )
    return send_message(chat_id=chat_id, text=text, settings=settings)


def _parse_message_id(raw: object) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _publish_list_message(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    chat_id: int,
    text: str,
    reply_markup: dict | None,
    settings: Settings,
) -> None:
    """Send a list response and keep only the latest list keyboard active."""
    raw_ctx = ctx_svc.get(user)
    if isinstance(raw_ctx, dict):
        ctx = raw_ctx
    elif isinstance(user.context, dict):
        ctx = user.context
    else:
        ctx = {}
    previous_message_id = _parse_message_id(ctx.get("active_list_message_id"))

    sent_message_id = _send_with_optional_keyboard(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        settings=settings,
    )
    if sent_message_id is None:
        return

    if previous_message_id is not None and previous_message_id != sent_message_id:
        edit_message_reply_markup(
            chat_id=chat_id,
            message_id=previous_message_id,
            reply_markup={"inline_keyboard": []},
            settings=settings,
        )

    has_keyboard = bool(reply_markup and reply_markup.get("inline_keyboard"))
    ctx_svc.set_fields(
        user,
        active_list_message_id=(sent_message_id if has_keyboard else None),
    )
    db.commit()


def _safe_page(page: int, total_items: int) -> int:
    total_pages = max(1, math.ceil(total_items / _LIST_PAGE_SIZE))
    if page < 0:
        return 0
    return min(page, total_pages - 1)


def _render_team_page(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    page: int,
) -> tuple[str, dict | None]:
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    restaurants = RestaurantService(db).list_for_user(user_id=user.id)
    restaurant_name = next(
        (r.name for r, _ in restaurants if r.id == restaurant_id),
        "Restaurant",
    )

    members = RestaurantService(db).list_members(restaurant_id=restaurant_id)
    total = len(members)
    if total == 0:
        raise ValueError("No team members found.")

    page = _safe_page(page, total)
    offset = page * _LIST_PAGE_SIZE
    page_members = members[offset : offset + _LIST_PAGE_SIZE]

    ctx_svc.set_numbered_items(user, [u.id for u, _ in page_members])
    ctx_svc.set_list_state(user, "team", offset)

    lines = [f"👥 Team — {restaurant_name}\n"]
    for i, (member, mem_record) in enumerate(page_members, offset + 1):
        role = "Owner" if mem_record.is_owner else "Member"
        joined = mem_record.joined_at.strftime("%b %Y")
        name = member.full_name or member.username or "Unknown"
        lines.append(f"{i}. {name} ({role}) — since {joined}")

    total_pages = max(1, math.ceil(total / _LIST_PAGE_SIZE))
    lines.append(f"\n{total} member{'s' if total != 1 else ''} total.")
    if total_pages > 1:
        lines.append(f"Page {page + 1}/{total_pages}")
    lines.append("No pending invites.")

    keyboard = _pagination_markup(
        list_type="team",
        current_page=page,
        total_pages=total_pages,
    )
    return "\n".join(lines), keyboard


def _render_products_page(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    page: int,
) -> tuple[str, dict | None]:
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    svc = SupplierService(db)
    total = svc.count_products_for_restaurant(restaurant_id=restaurant_id)
    if total == 0:
        raise ValueError("📦 No products yet. Upload a price list to get started.")

    page = _safe_page(page, total)
    offset = page * _LIST_PAGE_SIZE
    products = svc.list_products_for_restaurant(
        restaurant_id=restaurant_id,
        offset=offset,
        limit=_LIST_PAGE_SIZE,
    )

    ctx_svc.set_numbered_items(user, [price.id for _, price in products])
    ctx_svc.set_list_state(user, "products", offset)

    # Pre-fetch last-updated metadata for each unique supplier on this page
    supplier_meta: dict = {}
    for supplier, _ in products:
        if supplier.id not in supplier_meta:
            last_updated, _ = svc.get_price_list_meta(restaurant_id, supplier.id)
            supplier_meta[supplier.id] = last_updated

    lines = ["📦 **Products**\n"]
    current_supplier_id = None
    for i, (supplier, price) in enumerate(products, offset + 1):
        if supplier.id != current_supplier_id:
            last_updated = supplier_meta.get(supplier.id)
            date_suffix = f" · {last_updated.strftime('%-d %b')}" if last_updated else ""
            lines.append(f"\n{format_supplier_name(supplier.name)}{date_suffix}:")
            current_supplier_id = supplier.id
        price_str = format_price_monospace(price.price_minor, price.price_exp, price.currency)
        unit = f"/{price.unit}" if price.unit else ""
        item_with_emoji = format_with_emoji(price.item_name)
        lines.append(f"{i}. {item_with_emoji} — {price_str}{unit}")

    total_pages = max(1, math.ceil(total / _LIST_PAGE_SIZE))
    footer_text = f"📊 {total} product{'s' if total != 1 else ''} total"
    if total_pages > 1:
        footer_text += f" • Page {page + 1}/{total_pages}"
    
    lines.append("")  # Empty line before footer
    lines.append(format_footer(footer_text, divider=True))

    keyboard = _pagination_markup(
        list_type="products",
        current_page=page,
        total_pages=total_pages,
    )
    return "\n".join(lines), keyboard


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _render_prices_page(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    page: int,
    supplier_id: uuid.UUID,
    supplier_name: str | None = None,
) -> tuple[str, dict | None]:
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    svc = SupplierService(db)
    total = svc.count_prices_for_supplier(
        restaurant_id=restaurant_id,
        supplier_id=supplier_id,
    )
    if total == 0:
        supplier_label = supplier_name or "that supplier"
        raise ValueError(
            f"No prices found for {supplier_label}. Upload a price list to get started."
        )

    page = _safe_page(page, total)
    offset = page * _LIST_PAGE_SIZE
    prices = svc.list_prices_for_supplier(
        restaurant_id=restaurant_id,
        supplier_id=supplier_id,
        offset=offset,
        limit=_LIST_PAGE_SIZE,
    )

    if supplier_name is None:
        supplier = db.get(Supplier, supplier_id)
        supplier_name = supplier.name if supplier else "Supplier"

    # Get last updated timestamp and uploader name
    last_updated, uploader_name = svc.get_price_list_meta(restaurant_id, supplier_id)

    ctx_svc.set_numbered_items(user, [price.id for price, _ in prices])
    ctx_svc.set_list_state(user, "prices", offset)
    ctx_svc.set_fields(user, prices_supplier_id=str(supplier_id))

    lines = [f"💰 Prices — {format_supplier_name(supplier_name)}"]
    if last_updated:
        # Format: 12 June 2026 5:35 AM
        last_updated_str = last_updated.strftime('%-d %B %Y %-I:%M %p UTC')
        lines.append(f"Last updated: {last_updated_str}")
    if uploader_name:
        lines.append(f"Updated by: {uploader_name}")
    lines.append("")  # blank line before items

    for i, (price, effective_date) in enumerate(prices, offset + 1):
        price_str = format_price_monospace(price.price_minor, price.price_exp, price.currency)
        unit = f"/{price.unit}" if price.unit else ""
        date_str = (
            f" (updated {effective_date.strftime('%b %d')})"
            if effective_date
            else ""
        )
        item_with_emoji = format_with_emoji(price.item_name)
        lines.append(f"{i}. {item_with_emoji} — {price_str}{unit}{date_str}")

    total_pages = max(1, math.ceil(total / _LIST_PAGE_SIZE))
    footer_text = f"📊 {total} price{'s' if total != 1 else ''} total"
    if total_pages > 1:
        footer_text += f" • Page {page + 1}/{total_pages}"
    
    lines.append("")  # Empty line before footer
    lines.append(format_footer(footer_text, divider=True))

    keyboard = _pagination_markup(
        list_type="prices",
        current_page=page,
        total_pages=total_pages,
    )
    return "\n".join(lines), keyboard


def _render_inventory_page(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    page: int,
) -> tuple[str, dict | None]:
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    inv_svc = InventoryService(db)
    total = inv_svc.count_items(restaurant_id)
    if total == 0:
        raise ValueError("📦 No inventory data yet. Upload an invoice to get started.")

    page = _safe_page(page, total)
    offset = page * _LIST_PAGE_SIZE
    items = inv_svc.list_items(restaurant_id, offset=offset, limit=_LIST_PAGE_SIZE)

    ctx_svc.set_numbered_items(user, [item.id for item, _ in items])
    ctx_svc.set_list_state(user, "inventory", offset)

    last_movement = inv_svc.get_last_movement_time(restaurant_id)
    lines = ["📦 **Inventory**"]
    if last_movement:
        lines.append(f"Last movement: {last_movement.strftime('%-d %B %Y %-I:%M %p UTC')}")
    lines.append("")
    for i, (item, balance) in enumerate(items, offset + 1):
        bal_val = float(balance.balance) if balance is not None else 0.0
        unit = f" {item.unit}" if item.unit else ""
        prefix = "⚠️ " if bal_val < 0 else ""
        item_with_emoji = format_with_emoji(item.name)
        lines.append(f"{i}. {prefix}{item_with_emoji} — {bal_val:g}{unit}")

    total_pages = max(1, math.ceil(total / _LIST_PAGE_SIZE))
    footer_text = f"📊 {total} item{'s' if total != 1 else ''} total"
    if total_pages > 1:
        footer_text += f" • Page {page + 1}/{total_pages}"

    lines.append("")  # Empty line before footer
    lines.append(format_footer(footer_text, divider=True))

    # Build keyboard: quick-adjust rows + pagination
    adj_rows = quick_adj_rows(items)
    pag_keyboard = _pagination_markup(
        list_type="inventory",
        current_page=page,
        total_pages=total_pages,
    )
    pag_rows = (pag_keyboard or {}).get("inline_keyboard", [])
    combined_rows = adj_rows + pag_rows
    keyboard = {"inline_keyboard": combined_rows} if combined_rows else None
    return "\n".join(lines), keyboard


def _render_suppliers_page(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    page: int,
) -> tuple[str, dict | None]:
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    svc = SupplierService(db)
    total = svc.count_for_restaurant(restaurant_id=restaurant_id)
    if total == 0:
        raise ValueError("No suppliers linked yet. Use /add supplier to add one.")

    page = _safe_page(page, total)
    offset = page * _LIST_PAGE_SIZE
    suppliers = svc.list_for_restaurant(
        restaurant_id=restaurant_id,
        offset=offset,
        limit=_LIST_PAGE_SIZE,
    )

    ctx_svc.set_numbered_items(user, [s.id for s in suppliers])
    ctx_svc.set_list_state(user, "suppliers", offset)

    lines = ["📋 Suppliers\n"]
    for i, supplier in enumerate(suppliers, offset + 1):
        lines.append(f"{i}. {supplier.name}")

    total_pages = max(1, math.ceil(total / _LIST_PAGE_SIZE))
    if total_pages > 1:
        lines.append(f"\nPage {page + 1}/{total_pages}")

    keyboard = _pagination_markup(
        list_type="suppliers",
        current_page=page,
        total_pages=total_pages,
    )
    return "\n".join(lines), keyboard


def _render_item_search_results(
    *,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
    query: str,
) -> str:
    """Render item-first search results across all suppliers.
    
    Returns formatted message showing prices for the searched item
    from all suppliers that have it.
    """
    restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
    if restaurant_id is None:
        raise ValueError("No active restaurant. Send /start to complete setup.")

    svc = SupplierService(db)
    results = svc.search_items_across_suppliers(
        restaurant_id=restaurant_id,
        query=query,
        threshold=0.3,
        limit=20,
    )

    if not results:
        raise ValueError(f'No items found matching "{query}".')

    # Group by supplier for better presentation
    lines = [f"🔍 **Item Search:** {query}\n"]
    
    current_supplier_name = None
    supplier_results = []
    
    for price, supplier, effective_date, similarity_score in results:
        if supplier.name != current_supplier_name:
            if supplier_results:
                lines.append("")  # empty line before next supplier
            lines.append(f"{format_supplier_name(supplier.name)}:")
            current_supplier_name = supplier.name
            supplier_results = []
        
        price_str = format_price_monospace(price.price_minor, price.price_exp, price.currency)
        unit = f"/{price.unit}" if price.unit else ""
        date_str = (
            f" (updated {effective_date.strftime('%b %d')})"
            if effective_date
            else ""
        )
        item_with_emoji = format_with_emoji(price.item_name)
        # Show similarity score if less than 0.8 (not exact match)
        score_indicator = f" ({similarity_score:.0%})" if similarity_score < 0.8 else ""
        lines.append(f"• {item_with_emoji} — {price_str}{unit}{date_str}{score_indicator}")

    total = len(results)
    lines.append("")
    lines.append(format_footer(f"📊 Found {total} price{'s' if total != 1 else ''} across {len(set(r[1].name for r in results))} supplier{'s' if len(set(r[1].name for r in results)) != 1 else ''}", divider=True))
    
    return "\n".join(lines)


def build_list_page(
    *,
    list_type: str,
    page: int,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> tuple[str, dict | None]:
    """Build list text + keyboard for callback pagination/edit-in-place updates."""
    if list_type == "team":
        return _render_team_page(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            page=page,
        )
    if list_type == "products":
        return _render_products_page(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            page=page,
        )
    if list_type == "prices":
        supplier_id = _parse_uuid((user.context or {}).get("prices_supplier_id"))
        if supplier_id is None:
            raise ValueError("Prices page expired. Run /prices <supplier name> again.")
        return _render_prices_page(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            page=page,
            supplier_id=supplier_id,
        )
    if list_type == "inventory":
        return _render_inventory_page(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            page=page,
        )
    if list_type == "suppliers":
        return _render_suppliers_page(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            page=page,
        )
    raise ValueError("Unsupported list type.")


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Dispatch slash commands to appropriate handlers.

    Args:
        update: Telegram update dict
        user: User ORM object
        db: Database session
        ctx_svc: ContextService for managing user.context
        settings: App settings
    """
    chat_id = user.chat_id
    msg = update.get("message", {})
    text = (msg.get("text") or "").strip()

    command, args = _parse_command(text)

    # --- /help ---
    if command == "help":
        send_message(chat_id=chat_id, text=HELP_TEXT, settings=settings)
        return

    # --- /start (handled by reset handler, but just in case) ---
    if command == "start":
        from app.telegram.handlers.reset import handle as reset_handle

        reset_handle(update, user, db, ctx_svc, settings)
        return

    # --- /profile ---
    if command == "profile":
        restaurants = RestaurantService(db).list_for_user(user_id=user.id)
        if not restaurants:
            # No restaurant yet — redirect to onboarding
            send_message(
                chat_id=chat_id,
                text="You haven't set up your restaurant yet. Send /start to complete setup.",
                settings=settings,
            )
            return

        # Find active restaurant, fall back to first
        active_id = ctx_svc.get_active_restaurant_id(user)
        restaurant, membership = restaurants[0]
        for r, m in restaurants:
            if r.id == active_id:
                restaurant, membership = r, m
                break

        role = "Owner" if membership.is_owner else "Member"
        joined = membership.joined_at.strftime("%b %Y")

        lines = ["👤 Profile\n"]
        lines.append(f"Name: {user.full_name or '—'}")
        if user.username:
            lines.append(f"Username: @{user.username}")
        lines.append(f"Restaurant: {restaurant.name}")
        lines.append(f"Role: {role}")
        lines.append(f"Member since: {joined}")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /team ---
    if command == "team":
        try:
            text_out, keyboard = build_list_page(
                list_type="team",
                page=0,
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                settings=settings,
            )
        except ValueError as exc:
            send_message(chat_id=chat_id, text=str(exc), settings=settings)
            return

        db.commit()
        _publish_list_message(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            chat_id=chat_id,
            text=text_out,
            reply_markup=keyboard,
            settings=settings,
        )
        return

    # --- /outlets ---
    if command == "outlets":
        restaurants = RestaurantService(db).list_for_user(user_id=user.id)

        if not restaurants:
            send_message(
                chat_id=chat_id,
                text="You're not part of any restaurant yet. Send /start to set one up.",
                settings=settings,
            )
            return

        active_id = ctx_svc.get_active_restaurant_id(user)

        lines = ["🏪 Your Restaurant\n"]
        for r, _ in restaurants:
            marker = "✅ " if r.id == active_id else ""
            lines.append(f"{marker}{r.name}")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /products ---
    if command == "products":
        try:
            text_out, keyboard = build_list_page(
                list_type="products",
                page=0,
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                settings=settings,
            )
        except ValueError as exc:
            send_message(chat_id=chat_id, text=str(exc), settings=settings)
            return

        db.commit()
        _publish_list_message(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            chat_id=chat_id,
            text=text_out,
            reply_markup=keyboard,
            settings=settings,
        )
        return

    # --- /prices ---
    if command == "prices":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Send /start to complete setup.",
                settings=settings,
            )
            return

        if not args:
            send_message(
                chat_id=chat_id,
                text="Usage: /prices <supplier or item name>\nExample: /prices ABC Wholesalers\nExample: /prices chicken breast",
                settings=settings,
            )
            return

        svc = SupplierService(db)
        # First try to match a supplier
        matches = svc.fuzzy_search_for_restaurant(
            args,
            restaurant_id=restaurant_id,
            threshold=0.6,
        )
        if matches:
            # Supplier found: show prices from that supplier
            supplier, _ = matches[0]
            try:
                text_out, keyboard = _render_prices_page(
                    user=user,
                    db=db,
                    ctx_svc=ctx_svc,
                    settings=settings,
                    page=0,
                    supplier_id=supplier.id,
                    supplier_name=supplier.name,
                )
            except ValueError as exc:
                send_message(chat_id=chat_id, text=str(exc), settings=settings)
                return

            db.commit()
            _publish_list_message(
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                chat_id=chat_id,
                text=text_out,
                reply_markup=keyboard,
                settings=settings,
            )
            return
        
        # No supplier found: try item-first search across all suppliers
        try:
            item_results_text = _render_item_search_results(
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                settings=settings,
                query=args,
            )
            send_message(chat_id=chat_id, text=item_results_text, settings=settings)
            return
        except ValueError:
            # Item search also failed - show supplier suggestions as before
            pass

        # Neither supplier nor item found - show supplier suggestions
        suggestions = svc.fuzzy_search_for_restaurant(
            args,
            restaurant_id=restaurant_id,
            threshold=0.2,
        )
        if suggestions:
            unique_names: list[str] = []
            for candidate, _score in suggestions:
                if candidate.name in unique_names:
                    continue
                unique_names.append(candidate.name)
                if len(unique_names) >= 3:
                    break
            suggestion_lines = [f"• {name}" for name in unique_names]
            send_message(
                chat_id=chat_id,
                text=(
                    f'No supplier or item found matching "{args}".\n\n'
                    "Did you mean these suppliers?\n"
                    f"{'\n'.join(suggestion_lines)}\n\n"
                    "Use /prices <supplier name> or /list suppliers."
                ),
                settings=settings,
            )
            return
        send_message(
            chat_id=chat_id,
            text=f'No supplier or item found matching "{args}". Use /list suppliers to see your suppliers.',
            settings=settings,
        )
        return

    # --- /inventory ---
    if command == "inventory":
        try:
            text_out, keyboard = build_list_page(
                list_type="inventory",
                page=0,
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                settings=settings,
            )
        except ValueError as exc:
            send_message(chat_id=chat_id, text=str(exc), settings=settings)
            return

        db.commit()
        _publish_list_message(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            chat_id=chat_id,
            text=text_out,
            reply_markup=keyboard,
            settings=settings,
        )
        return

    # --- /balance ---
    if command == "balance":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Send /start to complete setup.",
                settings=settings,
            )
            return

        inv_svc = InventoryService(db)
        summary = inv_svc.get_balance_summary(restaurant_id)

        if summary.total_items == 0:
            send_message(
                chat_id=chat_id,
                text="📊 No inventory data yet. Upload an invoice to get started.",
                settings=settings,
            )
            return

        zero_flag = " ⚠️" if summary.zero_stock_count > 0 else ""
        neg_flag = " ⚠️" if summary.negative_count > 0 else ""

        negative_items = inv_svc.list_negative_items(restaurant_id=restaurant_id, limit=5)
        zero_items = inv_svc.list_zero_stock_items(restaurant_id=restaurant_id, limit=5)

        lines = [
            "📊 Stock Summary\n",
            f"Total items: {summary.total_items}",
            f"Zero stock: {summary.zero_stock_count}{zero_flag}",
            f"Negative balance: {summary.negative_count}{neg_flag}",
        ]

        if negative_items:
            lines.append("\nMost negative items:")
            for item, balance in negative_items:
                bal_val = float(balance.balance)
                unit = f" {item.unit}" if item.unit else ""
                lines.append(f"• {format_with_emoji(item.name)}: {bal_val:g}{unit}")
            if summary.negative_count > len(negative_items):
                lines.append(f"• +{summary.negative_count - len(negative_items)} more")

        if zero_items:
            lines.append("\nZero-stock items:")
            for item, _balance in zero_items:
                unit = f" ({item.unit})" if item.unit else ""
                lines.append(f"• {format_with_emoji(item.name)}{unit}")
            if summary.zero_stock_count > len(zero_items):
                lines.append(f"• +{summary.zero_stock_count - len(zero_items)} more")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /list suppliers ---
    if command == "list suppliers":
        try:
            text_out, keyboard = build_list_page(
                list_type="suppliers",
                page=0,
                user=user,
                db=db,
                ctx_svc=ctx_svc,
                settings=settings,
            )
        except ValueError as exc:
            send_message(chat_id=chat_id, text=str(exc), settings=settings)
            return

        db.commit()
        _publish_list_message(
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            chat_id=chat_id,
            text=text_out,
            reply_markup=keyboard,
            settings=settings,
        )
        return

    # --- /add supplier ---
    if command == "add supplier":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Send /start to complete setup.",
                settings=settings,
            )
            return

        if not args:
            send_message(
                chat_id=chat_id,
                text="What's the supplier name? Example: /add supplier ABC Wholesalers",
                settings=settings,
            )
            return

        svc = SupplierService(db)

        # Check for existing suppliers with similar names (global search for creation check).
        # If a close match exists, link it deterministically rather than prompting yes/no.
        matches = svc.fuzzy_search(args, threshold=0.75)

        if matches:
            existing_supplier, _ = matches[0]
            try:
                svc.link(
                    supplier_id=existing_supplier.id,
                    restaurant_id=restaurant_id,
                    user_id=user.id,
                )
                db.commit()
                send_message(
                    chat_id=chat_id,
                    text=f"✅ Linked existing supplier: {existing_supplier.name}",
                    settings=settings,
                )
            except AlreadyLinkedError:
                send_message(
                    chat_id=chat_id,
                    text=f"ℹ️ {existing_supplier.name} is already linked to your restaurant.",
                    settings=settings,
                )
            return

        # No match — create new supplier
        supplier = svc.create(
            name=args,
            user_id=user.id,
            restaurant_id=restaurant_id,
        )
        db.commit()

        send_message(
            chat_id=chat_id,
            text=f"✅ Supplier added: {supplier.name}",
            settings=settings,
        )
        return

    # --- /link supplier ---
    if command == "link supplier":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Send /start to complete setup.",
                settings=settings,
            )
            return

        if not args:
            send_message(
                chat_id=chat_id,
                text="Which supplier do you want to link? Example: /link supplier ABC Wholesalers",
                settings=settings,
            )
            return

        matches = SupplierService(db).fuzzy_search(args, threshold=0.75)

        if not matches:
            send_message(
                chat_id=chat_id,
                text=f'No supplier found matching "{args}". Use /add supplier to create a new one.',
                settings=settings,
            )
            return

        # Show matches
        lines = ["🔍 **Found suppliers:**\n"]
        for i, (s, score) in enumerate(matches[:5], 1):
            lines.append(f"{i}. {s.name} ({score:.0%} match)")

        lines.append("\nReply with the number to link, or /cancel to abort.")

        ctx_svc.set_numbered_items(user, [s.id for s, _ in matches[:5]])
        ctx_svc.set_fields(user, pending_action="link_supplier")
        db.commit()

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /uploads ---
    if command == "uploads":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            return

        from sqlalchemy import desc, select
        from app.db.models.file_processing_staging import FileProcessingStaging
        from app.db.models.restaurant_user import RestaurantUser
        from app.db.models.suppliers import Supplier
        from app.telegram.keyboards import cb_open_upload, make_button

        membership = db.scalar(
            select(RestaurantUser).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user.id,
                RestaurantUser.is_active.is_(True),
            )
        )
        if membership is None:
            send_message(chat_id=chat_id, text="Action unavailable.", settings=settings)
            return

        records_stmt = (
            select(FileProcessingStaging)
            .where(FileProcessingStaging.restaurant_id == restaurant_id)
            .where(FileProcessingStaging.status.in_(["processing", "pending_review", "error"]))
        )
        if not membership.is_owner:
            records_stmt = records_stmt.where(FileProcessingStaging.uploaded_by == user.id)

        records = db.scalars(
            records_stmt.order_by(desc(FileProcessingStaging.created_at)).limit(10)
        ).all()

        if not records:
            send_message(chat_id=chat_id, text="No pending uploads.", settings=settings)
            return

        _STATUS_LABEL = {
            "processing":    "⏳ Extracting",
            "pending_review": "👁 Awaiting review",
            "error":         "⚠️ Failed",
        }
        lines = ["📂 Pending uploads:\n"]
        keyboard_rows: list = []
        for r in records:
            label = _STATUS_LABEL.get(r.status, r.status)
            doc = (r.document_type or "?").replace("_", " ")
            date_str = r.created_at.strftime("%d %b %H:%M") if r.created_at else "?"
            supplier_name = ""
            if r.supplier_id:
                sup = db.get(Supplier, r.supplier_id)
                if sup:
                    supplier_name = f" — {sup.name}"
            err = f"\n   ↳ {r.error_message}" if r.status == "error" and r.error_message else ""
            lines.append(f"{label}  {doc} · {date_str}{supplier_name}{err}")
            if r.status == "pending_review":
                keyboard_rows.append([
                    make_button(
                        f"📂 Open {doc} · {date_str}{supplier_name}",
                        cb_open_upload(r.id),
                    )
                ])

        text = "\n".join(lines)
        if keyboard_rows:
            send_message_with_keyboard(
                chat_id=chat_id,
                text=text,
                reply_markup={"inline_keyboard": keyboard_rows},
                settings=settings,
            )
        else:
            send_message(chat_id=chat_id, text=text, settings=settings)
        return

    # --- /chart ---
    if command == "chart":
        from app.services.chart_service import make_stock_chart
        from app.db.models.restaurant import Restaurant

        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(chat_id=chat_id, text="No active restaurant. Send /start to set up.", settings=settings)
            return

        inv_svc = InventoryService(db)
        total = inv_svc.count_items(restaurant_id)
        if total == 0:
            send_message(chat_id=chat_id, text="📦 No inventory data yet. Upload an invoice to get started.", settings=settings)
            return

        items = inv_svc.list_items(restaurant_id, offset=0, limit=15)
        restaurant = db.get(Restaurant, restaurant_id)
        rest_name = restaurant.name if restaurant else "Inventory"

        image_bytes = make_stock_chart(items, rest_name)
        send_photo(
            chat_id=chat_id,
            image_bytes=image_bytes,
            caption=f"📦 {rest_name} — stock levels ({total} items)",
            settings=settings,
        )
        return

    # --- /history <item name> ---
    if command == "history":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(chat_id=chat_id, text="No active restaurant. Send /start to set up.", settings=settings)
            return

        if not args:
            send_message(chat_id=chat_id, text="Usage: /history <item name>\nExample: /history onion", settings=settings)
            return

        inv_svc = InventoryService(db)
        matches = inv_svc.fuzzy_match_item(restaurant_id=restaurant_id, name=args, threshold=0.45)
        if not matches:
            send_message(chat_id=chat_id, text=f'No inventory item matching "{args}". Try /inventory to see all items.', settings=settings)
            return

        item, score = matches[0]
        txns = inv_svc.get_item_transactions(restaurant_id=restaurant_id, item_id=item.id, limit=10)
        if not txns:
            send_message(chat_id=chat_id, text=f"No transactions recorded for {item.name} yet.", settings=settings)
            return

        unit_str = f" {item.unit}" if item.unit else ""
        lines = [f"📋 {format_with_emoji(item.name)} — last {len(txns)} transactions\n"]
        for txn in txns:
            arrow = "➕" if txn.txn_type == "credit" else "➖"
            src = f" ({txn.source})" if txn.source != "manual" else ""
            date_str = txn.created_at.strftime("%-d %b %H:%M") if txn.created_at else "?"
            lines.append(f"{arrow} {float(txn.quantity):g}{unit_str}{src}  ·  {date_str}")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /export ---
    if command == "export":
        import csv
        import io

        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(chat_id=chat_id, text="No active restaurant. Send /start to set up.", settings=settings)
            return

        inv_svc = InventoryService(db)
        total = inv_svc.count_items(restaurant_id)
        if total == 0:
            send_message(chat_id=chat_id, text="📦 No inventory data yet. Upload an invoice to get started.", settings=settings)
            return

        # Fetch all items (no pagination for export)
        items = inv_svc.list_items(restaurant_id, offset=0, limit=total)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Item", "Unit", "Balance"])
        for item, balance in items:
            bal = float(balance.balance) if balance is not None else 0.0
            writer.writerow([item.name, item.unit or "", f"{bal:g}"])

        from app.db.models.restaurant import Restaurant
        restaurant = db.get(Restaurant, restaurant_id)
        rest_name = (restaurant.name if restaurant else "inventory").replace(" ", "_")
        import datetime as _dt
        date_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
        filename = f"{rest_name}_{date_str}.csv"

        send_document(
            chat_id=chat_id,
            file_bytes=buf.getvalue().encode("utf-8"),
            filename=filename,
            caption=f"📦 {total} items — {date_str}",
            settings=settings,
        )
        return

    # --- /search <query> ---
    if command == "search":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(chat_id=chat_id, text="No active restaurant. Send /start to set up.", settings=settings)
            return

        if not args:
            send_message(chat_id=chat_id, text="Usage: /search <query>\nExample: /search black truffle", settings=settings)
            return

        from app.services.money import to_display

        inv_svc = InventoryService(db)
        sup_svc = SupplierService(db)

        lines: list[str] = [f"🔍 Results for \"{args}\"\n"]
        found_any = False

        # 1. Inventory items
        inv_hits = inv_svc.fuzzy_match_item(restaurant_id=restaurant_id, name=args, threshold=0.3)
        if inv_hits:
            found_any = True
            lines.append(f"📦 Inventory ({len(inv_hits)} item{'s' if len(inv_hits) != 1 else ''}):")
            from sqlalchemy import select as _sa_select
            from app.db.models.inventory_balances import InventoryBalance
            for item, score in inv_hits[:5]:
                bal = db.scalar(
                    _sa_select(InventoryBalance.balance).where(
                        InventoryBalance.item_id == item.id,
                        InventoryBalance.restaurant_id == restaurant_id,
                    )
                )
                bal_str = f"{float(bal):g}" if bal is not None else "0"
                unit_str = f" {item.unit}" if item.unit else ""
                score_str = f" ({score:.0%})" if score < 0.8 else ""
                lines.append(f"  • {format_with_emoji(item.name)}: {bal_str}{unit_str}{score_str}")

        # 2. Supplier products (prices)
        price_hits = sup_svc.search_items_across_suppliers(
            restaurant_id=restaurant_id, query=args, threshold=0.3, limit=8
        )
        if price_hits:
            found_any = True
            lines.append(f"\n🛒 Products ({len(price_hits)} result{'s' if len(price_hits) != 1 else ''}):")
            for price, supplier, _eff_date, score in price_hits[:8]:
                price_val = to_display(price.price_minor, price.price_exp)
                currency = price.currency or supplier.default_currency or ""
                unit_str = f"/{price.unit}" if price.unit else ""
                score_str = f" ({score:.0%})" if score < 0.8 else ""
                lines.append(
                    f"  • {format_with_emoji(price.item_name)}{score_str}"
                    f" — {supplier.name}"
                    f" @ {price_val:g} {currency}{unit_str}"
                )

        # 3. Suppliers by name
        sup_hits = sup_svc.fuzzy_search_for_restaurant(
            name=args, restaurant_id=restaurant_id, threshold=0.4
        )
        if sup_hits:
            found_any = True
            lines.append(f"\n🏪 Suppliers ({len(sup_hits)}):")
            for supplier, score in sup_hits[:5]:
                score_str = f" ({score:.0%})" if score < 0.8 else ""
                lines.append(f"  • {format_supplier_name(supplier.name)}{score_str}")

        if not found_any:
            lines.append(f"No results found for \"{args}\".")
            lines.append("Try a shorter or different search term.")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # Unknown command
    send_message(
        chat_id=chat_id,
        text=f"Unknown command: {command}\n\n{HELP_TEXT}",
        settings=settings,
    )
