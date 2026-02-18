"""Priority 3: Slash command handler.

Commands:
    /profile           — Your profile
    /team              — Staff & invites
    /list suppliers    — list this restaurant's linked suppliers
    /add supplier      — create a new supplier (search global + create if not found)
    /link supplier     — link an existing global supplier to restaurant
    /outlets           — Your restaurants
    /products          — Supplier product catalog
    /prices <name>     — Prices from a supplier
    /inventory         — Stock levels
    /balance           — Stock summary
    /uploads           — list pending staging records
    /switch            — change active restaurant
    /help              — show available commands
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.money import format_price
from app.services.restaurant_service import RestaurantService
from app.services.supplier_service import AlreadyLinkedError, SupplierService
from app.telegram.bot_api import send_message


HELP_TEXT = """Available commands:

/profile           — Your profile
/team              — Staff & invites
/list suppliers    — Show your linked suppliers
/add supplier <name> — Add a new supplier
/link supplier <name> — Link an existing supplier
/outlets           — Your restaurants
/products          — Supplier product catalog
/prices <name>     — Prices from a supplier
/inventory         — Stock levels
/balance           — Stock summary
/uploads           — Pending uploads
/switch            — Switch restaurant
/help              — Show this message"""


def _require_active_restaurant(
    user: User,
    ctx_svc: ContextService,
    db: Session,
    settings: Settings,
) -> uuid.UUID | None:
    """Return active_restaurant_id or None if not set.

    If user has no active restaurant but has exactly one restaurant,
    auto-set it. Otherwise, prompt /switch.

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
        (r"^/switch\b", "switch"),
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
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        # Get restaurant name from user's restaurant list
        restaurants = RestaurantService(db).list_for_user(user_id=user.id)
        restaurant_name = next(
            (r.name for r, _ in restaurants if r.id == restaurant_id), "Restaurant"
        )

        members = RestaurantService(db).list_members(restaurant_id=restaurant_id)

        page_members = members[:10]
        ctx_svc.set_numbered_items(user, [u.id for u, _ in page_members])
        ctx_svc.set_list_state(user, "team", 0)
        db.commit()

        lines = [f"👥 Team — {restaurant_name}\n"]
        for i, (member, mem_record) in enumerate(page_members, 1):
            role = "Owner" if mem_record.is_owner else "Member"
            joined = mem_record.joined_at.strftime("%b %Y")
            name = member.full_name or member.username or "Unknown"
            lines.append(f"{i}. {name} ({role}) — since {joined}")

        total = len(members)
        lines.append(f"\n{total} member{'s' if total != 1 else ''} total.")
        if total > 10:
            lines.append(f"Page 1/{(total + 9) // 10}  [Next ▶]")
        lines.append("No pending invites.")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
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
        ctx_svc.set_numbered_items(user, [r.id for r, _ in restaurants])
        ctx_svc.set_list_state(user, "restaurants", 0)
        ctx_svc.set_fields(user, pending_action="switch_restaurant")
        db.commit()

        lines = ["🏪 Your Restaurants\n"]
        for i, (r, _) in enumerate(restaurants, 1):
            if r.id == active_id:
                lines.append(f"{i}. ✅ {r.name} (active)")
            else:
                lines.append(f"{i}. {r.name}")

        if len(restaurants) > 1:
            lines.append("\nReply #N to switch.")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /products ---
    if command == "products":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        svc = SupplierService(db)
        products = svc.list_products_for_restaurant(
            restaurant_id=restaurant_id, offset=0, limit=10
        )

        if not products:
            send_message(
                chat_id=chat_id,
                text="📦 No products yet. Upload a price list to get started.",
                settings=settings,
            )
            return

        total = svc.count_products_for_restaurant(restaurant_id=restaurant_id)
        ctx_svc.set_numbered_items(user, [price.id for _, price in products])
        ctx_svc.set_list_state(user, "products", 0)
        db.commit()

        lines = ["📦 Products\n"]
        current_supplier_name = None
        for i, (supplier, price) in enumerate(products, 1):
            if supplier.name != current_supplier_name:
                lines.append(f"\n{supplier.name}:")
                current_supplier_name = supplier.name
            price_str = format_price(price.price_minor, price.price_exp, price.currency)
            unit = f"/{price.unit}" if price.unit else ""
            lines.append(f"{i}. {price.item_name} — {price_str}{unit}")

        total_pages = (total + 9) // 10
        if total_pages > 1:
            lines.append(f"\nPage 1/{total_pages}  [Next ▶]")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /prices ---
    if command == "prices":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        if not args:
            send_message(
                chat_id=chat_id,
                text="Usage: /prices <supplier name>\nExample: /prices ABC Wholesalers",
                settings=settings,
            )
            return

        svc = SupplierService(db)
        matches = svc.fuzzy_search_for_restaurant(args, restaurant_id=restaurant_id, threshold=0.6)
        if not matches:
            send_message(
                chat_id=chat_id,
                text=f'No supplier found matching "{args}". Use /list suppliers to see your suppliers.',
                settings=settings,
            )
            return

        supplier, _ = matches[0]
        prices = svc.list_prices_for_supplier(
            restaurant_id=restaurant_id,
            supplier_id=supplier.id,
            offset=0,
            limit=10,
        )

        if not prices:
            send_message(
                chat_id=chat_id,
                text=f"No prices found for {supplier.name}. Upload a price list to get started.",
                settings=settings,
            )
            return

        total = svc.count_prices_for_supplier(
            restaurant_id=restaurant_id, supplier_id=supplier.id
        )
        ctx_svc.set_numbered_items(user, [price.id for price, _ in prices])
        ctx_svc.set_list_state(user, "prices", 0)
        db.commit()

        lines = [f"💰 Prices — {supplier.name}\n"]
        for i, (price, effective_date) in enumerate(prices, 1):
            price_str = format_price(price.price_minor, price.price_exp, price.currency)
            unit = f"/{price.unit}" if price.unit else ""
            date_str = (
                f" (updated {effective_date.strftime('%b %d')})" if effective_date else ""
            )
            lines.append(f"{i}. {price.item_name} — {price_str}{unit}{date_str}")

        total_pages = (total + 9) // 10
        if total_pages > 1:
            lines.append(f"\nPage 1/{total_pages}  [Next ▶]")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /inventory (stub) ---
    if command == "inventory":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        send_message(
            chat_id=chat_id,
            text="📦 No inventory data yet. Upload an invoice to get started.",
            settings=settings,
        )
        return

    # --- /balance (stub) ---
    if command == "balance":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        send_message(
            chat_id=chat_id,
            text="📊 No inventory data yet.",
            settings=settings,
        )
        return

    # --- /list suppliers ---
    if command == "list suppliers":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
                settings=settings,
            )
            return

        suppliers = SupplierService(db).list_for_restaurant(
            restaurant_id=restaurant_id, offset=0, limit=10
        )

        if not suppliers:
            send_message(
                chat_id=chat_id,
                text="No suppliers linked yet. Use /add supplier to add one.",
                settings=settings,
            )
            return

        # Store UUIDs in context for "#N" selection
        ctx_svc.set_numbered_items(user, [s.id for s in suppliers])
        ctx_svc.set_list_state(user, "suppliers", 0)
        db.commit()

        lines = ["📋 **Suppliers:**\n"]
        for i, s in enumerate(suppliers, 1):
            lines.append(f"{i}. {s.name}")

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # --- /add supplier ---
    if command == "add supplier":
        restaurant_id = _require_active_restaurant(user, ctx_svc, db, settings)
        if restaurant_id is None:
            send_message(
                chat_id=chat_id,
                text="No active restaurant. Use /switch to select one.",
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
                text="No active restaurant. Use /switch to select one.",
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
        # TODO: Implement when staging service is ready
        send_message(
            chat_id=chat_id,
            text="No pending uploads.",
            settings=settings,
        )
        return

    # --- /switch ---
    if command == "switch":
        restaurants = RestaurantService(db).list_for_user(user_id=user.id)

        if not restaurants:
            send_message(
                chat_id=chat_id,
                text="You don't have any restaurants yet.",
                settings=settings,
            )
            return

        if len(restaurants) == 1:
            restaurant, _ = restaurants[0]
            ctx_svc.set_active_restaurant(user, restaurant.id)
            db.commit()
            send_message(
                chat_id=chat_id,
                text=f"Active restaurant: {restaurant.name}",
                settings=settings,
            )
            return

        lines = ["🔄 **Select restaurant:**\n"]
        for i, (r, _) in enumerate(restaurants, 1):
            lines.append(f"{i}. {r.name}")

        lines.append("\nReply with the number to select.")

        ctx_svc.set_numbered_items(user, [r.id for r, _ in restaurants])
        ctx_svc.set_fields(user, pending_action="switch_restaurant")
        db.commit()

        send_message(chat_id=chat_id, text="\n".join(lines), settings=settings)
        return

    # Unknown command
    send_message(
        chat_id=chat_id,
        text=f"Unknown command: {command}\n\n{HELP_TEXT}",
        settings=settings,
    )
