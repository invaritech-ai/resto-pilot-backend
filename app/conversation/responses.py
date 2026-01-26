"""
Static response templates for the intent-driven bot.

All user-facing messages are defined here for consistency and easy modification.
"""

from __future__ import annotations

from typing import Any


# =============================================================================
# NAVIGATION & SYSTEM
# =============================================================================

MAIN_MENU = """Welcome! Here's what I can help you with:

📋 *Profile* - View or update your name/phone
🏪 *Outlets* - Manage your restaurants
👥 *Staff* - View or invite team members
📦 *Suppliers* - Manage vendors, view price lists & items
📄 *Invoices* - View past invoices
📊 *Inventory* - Track stock, locations, log usage
📤 *Files* - Upload price lists or invoices

Just tell me what you'd like to do!"""

CANT_HELP = "I can't help with that. Type /menu to see what I can do."

CANCEL_SUCCESS = "Cancelled. Type /menu to see available options."

CANCEL_NOTHING = "Nothing to cancel. Type /menu to see available options."


# =============================================================================
# ACKNOWLEDGMENTS
# =============================================================================

ACK_PROCESSING = "Got it..."

ACK_FILE_PROCESSING = "Got it. File received."


# =============================================================================
# HELP
# =============================================================================

HELP_PROFILE = """📋 *Profile* lets you view or update your name and phone.
Try: "show my profile" or "update my phone to +1 415 555 0101"."""

HELP_OUTLETS = """🏪 *Outlets* lets you list, add, or rename restaurants.
Try: "list outlets" or "add outlet Little Italy"."""

HELP_STAFF = """👥 *Staff* lets you list, invite, or remove team members.
Try: "list staff" or "invite staff to Main"."""

HELP_SUPPLIERS = """📦 *Suppliers* lets you list, add, view, update, deactivate vendors, link a supplier to another outlet, and see unlinked suppliers.
Try: "list suppliers", "show unlinked suppliers", or "link supplier Fresh Farms to outlet Mercato"."""

HELP_INVOICES = """📄 *Invoices* lets you list invoices or view one.
Try: "list invoices"."""

HELP_INVENTORY = """📊 *Inventory* lets you list stock/locations, add items, or log usage.
Try: "list inventory" or "log 2kg usage"."""

HELP_FILES = """📤 *Files* lets you upload price lists or invoices.
Try: "upload invoice" and send the file."""

HELP_INVITES = """✉️ *Invites* lets you list active invite links or move an invite.
Try: "list invites"."""


def help_topic(topic: str | None) -> str:
    """Return help text for a specific topic or the main menu."""
    if not isinstance(topic, str):
        return MAIN_MENU

    normalized = topic.strip().lower()
    mapping = {
        "profile": HELP_PROFILE,
        "outlets": HELP_OUTLETS,
        "outlet": HELP_OUTLETS,
        "restaurants": HELP_OUTLETS,
        "restaurant": HELP_OUTLETS,
        "staff": HELP_STAFF,
        "suppliers": HELP_SUPPLIERS,
        "supplier": HELP_SUPPLIERS,
        "invoices": HELP_INVOICES,
        "invoice": HELP_INVOICES,
        "inventory": HELP_INVENTORY,
        "files": HELP_FILES,
        "file": HELP_FILES,
        "invites": HELP_INVITES,
        "invite": HELP_INVITES,
    }
    return mapping.get(normalized, MAIN_MENU)


# =============================================================================
# PROFILE
# =============================================================================

def profile_view(name: str | None, phone: str | None, username: str | None) -> str:
    """Format profile view response."""
    lines = ["📋 *Your Profile*\n"]
    lines.append(f"Name: {name or 'Not set'}")
    lines.append(f"Phone: {phone or 'Not set'}")
    if username:
        lines.append(f"Username: @{username}")
    return "\n".join(lines)


PROFILE_NAME_UPDATED = "Updated your name to '{name}'."

PROFILE_PHONE_UPDATED = "Updated your phone to '{phone}'."

PROFILE_UPDATE_ERROR = "Couldn't update your profile. Please try again."


# =============================================================================
# OUTLETS (RESTAURANTS)
# =============================================================================

def outlets_list(outlets: list[dict[str, Any]]) -> str:
    """Format outlets list response."""
    if not outlets:
        return "You don't have any outlets yet. Would you like to add one?"
    
    lines = ["🏪 *Your Outlets*\n"]
    for i, outlet in enumerate(outlets, 1):
        role_badge = "👑" if outlet.get("role") == "owner" else "👤"
        lines.append(f"{i}. {role_badge} {outlet['name']}")
    return "\n".join(lines)


OUTLET_CREATED = "Created outlet '{name}'! You're the owner."

OUTLET_UPDATED = "Updated outlet name to '{name}'."

OUTLET_CREATE_ERROR = "Couldn't create the outlet. Please try again."

OUTLET_NOT_FOUND = "Outlet not found. Use /menu to see your outlets."

OUTLET_NO_ACCESS = "You don't have access to this outlet."


def outlet_select_prompt(outlets: list[dict[str, Any]]) -> str:
    """Prompt user to select an outlet."""
    lines = ["Which outlet?\n"]
    for i, outlet in enumerate(outlets, 1):
        lines.append(f"{i}. {outlet['name']}")
    return "\n".join(lines)


# =============================================================================
# STAFF
# =============================================================================

def staff_list(members: list[dict[str, Any]], outlet_name: str | None = None) -> str:
    """Format staff list response."""
    outlet_label = f" — {outlet_name}" if outlet_name else ""
    if not members:
        suffix = f" for {outlet_name}" if outlet_name else ""
        return f"No staff members yet{suffix}. Would you like to invite someone?"
    
    lines = [f"👥 *Staff Members{outlet_label}*\n"]
    for member in members:
        role_badge = "👑" if member.get("role") == "owner" else "👤"
        name = member.get("name") or "Unknown"
        username = f" (@{member['username']})" if member.get("username") else ""
        lines.append(f"{role_badge} {name}{username}")
    return "\n".join(lines)


def staff_invite_created(deep_link: str, role: str, restaurant_name: str) -> str:
    """Format invite creation response."""
    return f"""Created invite link for {role} at {restaurant_name}:

{deep_link}

Share this link with the person you want to invite. It expires in 30 days."""


STAFF_REVOKED = "Removed {name} from {restaurant}."

STAFF_REVOKE_ERROR = "Couldn't remove this staff member. Please try again."

STAFF_CANT_REVOKE_SELF = "You can't remove yourself. Transfer ownership first."

STAFF_NOT_OWNER = "Only outlet owners can manage staff."


# =============================================================================
# INVITES
# =============================================================================

def invites_list(invites: list[dict[str, Any]], restaurant_name: str) -> str:
    """Format invite codes list response."""
    if not invites:
        return f"No active invites for {restaurant_name}."

    lines = [f"🔗 *Active Invites - {restaurant_name}*\n"]
    for invite in invites:
        role = invite.get("role", "staff")
        expires = invite.get("expires_at", "Never")
        link = invite.get("link", "")
        code = invite.get("code", "")
        lines.append(f"{role.title()} • Expires: {expires}")
        if code:
            lines.append(f"Code: {code}")
        if link:
            lines.append(link)
    return "\n".join(lines)


INVITE_MOVE_NEED_CODE = "Which invite should I move? Paste the invite link or code."

INVITE_MOVE_NOT_ACTIVE = "That invite is not active anymore."

INVITE_MOVE_ALREADY_TARGET = "That invite already belongs to {restaurant}."

INVITE_MOVE_NOT_OWNER = "Only outlet owners can move invites."

INVITE_MOVE_SUCCESS = "Moved invite {code} to {restaurant}."


# =============================================================================
# SUPPLIERS
# =============================================================================

def suppliers_list(suppliers: list[dict[str, Any]]) -> str:
    """Format suppliers list response."""
    if not suppliers:
        return "No suppliers yet. Would you like to add one?"
    
    lines = ["📦 *Suppliers*\n"]
    for i, supplier in enumerate(suppliers, 1):
        lines.append(f"{i}. {supplier['name']}")
    return "\n".join(lines)


SUPPLIER_CREATED = "Added supplier '{name}'."

SUPPLIER_CREATE_CONFIRMATION = "I'll add supplier '{name}' to {restaurant_name}. Confirm?"

SUPPLIER_CREATE_CANCELLED = "Cancelled supplier creation."

SUPPLIER_UPDATED = "Updated supplier '{name}'."

SUPPLIER_CREATE_ERROR = "Couldn't add the supplier. Please try again."

SUPPLIER_NOT_FOUND = "Supplier not found."

SUPPLIER_DEACTIVATED = "Deactivated supplier '{name}'."

SUPPLIER_ALREADY_INACTIVE = "Supplier '{name}' is already inactive."

SUPPLIER_LINK_ALL_CONFIRMATION = (
    "I'll link supplier '{name}' to all your outlets ({count}). Confirm?"
)

SUPPLIER_LINK_ALL_CANCELLED = "Cancelled linking supplier to all outlets."

SUPPLIER_LINK_MULTI_CONFIRMATION = (
    "I'll link supplier '{name}' to these outlets: {outlets}. Confirm?"
)

SUPPLIER_LINK_MULTI_CANCELLED = "Cancelled linking supplier to selected outlets."

SUPPLIER_LINK_MULTI_SUPPLIERS_CONFIRMATION = (
    "I'll link suppliers {suppliers} to these outlets: {outlets}. Confirm?"
)

SUPPLIER_LINK_MULTI_SUPPLIERS_CANCELLED = "Cancelled linking suppliers to selected outlets."


def supplier_details(supplier: dict[str, Any]) -> str:
    """Format supplier details response."""
    lines = [f"📦 *{supplier['name']}*\n"]
    if supplier.get("currency"):
        lines.append(f"Currency: {supplier['currency']}")
    if supplier.get("lead_time_days"):
        lines.append(f"Lead time: {supplier['lead_time_days']} days")
    if supplier.get("notes"):
        lines.append(f"Notes: {supplier['notes']}")
    return "\n".join(lines)


def supplier_price_list(supplier_name: str, items: list[dict[str, Any]]) -> str:
    """Format supplier price list response."""
    if not items:
        return (
            f"No price list found for {supplier_name}. "
            f"Would you like to upload one?"
        )
    
    lines = [f"💰 *{supplier_name} - Price List*\n"]
    for item in items[:30]:
        name = item.get("name", "Unknown")
        price = item.get("price")
        unit = item.get("unit", "")
        currency = item.get("currency", "")
        if price is not None:
            lines.append(f"• {name}: {price} {currency}/{unit}")
        else:
            lines.append(f"• {name}: (no price)")
    
    if len(items) > 30:
        lines.append(f"\n...and {len(items) - 30} more items")
    
    return "\n".join(lines)


def supplier_items_list(supplier_name: str, items: list[dict[str, Any]]) -> str:
    """Format supplier items list response."""
    if not items:
        return f"No items found for {supplier_name}."
    
    lines = [f"📋 *{supplier_name} - Items Offered*\n"]
    for i, item in enumerate(items[:30], 1):
        name = item.get("name", "Unknown")
        sku = item.get("sku")
        unit = item.get("unit", "")
        sku_str = f" (SKU: {sku})" if sku else ""
        lines.append(f"{i}. {name}{sku_str} - {unit}")
    
    if len(items) > 30:
        lines.append(f"\n...and {len(items) - 30} more items")
    
    return "\n".join(lines)


# =============================================================================
# INVOICES
# =============================================================================

def invoices_list(invoices: list[dict[str, Any]]) -> str:
    """Format invoices list response."""
    if not invoices:
        return "No invoices recorded yet."
    
    lines = ["📄 *Recent Invoices*\n"]
    for inv in invoices[:20]:
        date = inv.get("date", "N/A")
        supplier = inv.get("supplier", "Unknown")
        total = inv.get("total")
        currency = inv.get("currency", "")
        status = inv.get("status", "")
        
        total_str = f"{total} {currency}" if total else "N/A"
        status_emoji = "✅" if status == "completed" else "⏳"
        lines.append(f"{status_emoji} {date} - {supplier}: {total_str}")
    
    if len(invoices) > 20:
        lines.append(f"\n...and {len(invoices) - 20} more invoices")
    
    return "\n".join(lines)


def invoice_details(invoice: dict[str, Any], line_items: list[dict[str, Any]]) -> str:
    """Format invoice details response."""
    lines = ["📄 *Invoice Details*\n"]
    lines.append(f"Date: {invoice.get('date', 'N/A')}")
    lines.append(f"Supplier: {invoice.get('supplier', 'Unknown')}")
    if invoice.get("invoice_number"):
        lines.append(f"Invoice #: {invoice['invoice_number']}")
    total = invoice.get("total")
    currency = invoice.get("currency", "")
    if total:
        lines.append(f"Total: {total} {currency}")
    lines.append(f"Status: {invoice.get('status', 'N/A')}")
    
    if line_items:
        lines.append("\n*Line Items:*")
        for i, li in enumerate(line_items[:20], 1):
            desc = li.get("description", "Unknown")
            qty = li.get("quantity")
            unit = li.get("unit", "")
            unit_price = li.get("unit_price")
            total_amt = li.get("total")
            
            qty_str = f"{qty} {unit}" if qty else ""
            price_str = f"@ {unit_price}" if unit_price else ""
            total_str = f"= {total_amt}" if total_amt else ""
            
            lines.append(f"{i}. {desc} {qty_str} {price_str} {total_str}".strip())
        
        if len(line_items) > 20:
            lines.append(f"...and {len(line_items) - 20} more items")
    
    return "\n".join(lines)


# =============================================================================
# INVENTORY
# =============================================================================

def inventory_list(batches: list[dict[str, Any]]) -> str:
    """Format inventory list response."""
    if not batches:
        return "No inventory recorded yet."
    
    lines = ["📊 *Current Inventory*\n"]
    for batch in batches[:20]:  # Limit to 20 items
        qty = batch.get("quantity", 0)
        unit = batch.get("unit", "")
        product = batch.get("product_name", "Unknown")
        status = batch.get("status", "")
        status_emoji = "✅" if status == "available" else "⚠️"
        lines.append(f"{status_emoji} {product}: {qty} {unit}")
    
    if len(batches) > 20:
        lines.append(f"\n...and {len(batches) - 20} more items")
    
    return "\n".join(lines)


INVENTORY_ADDED = "Added {quantity} {unit} of {product}."

INVENTORY_UPDATED = "Updated inventory: {movement_type} {quantity} {unit}."

INVENTORY_ERROR = "Couldn't update inventory. Please try again."


def locations_list(locations: list[dict[str, Any]]) -> str:
    """Format inventory locations list response."""
    if not locations:
        return "No storage locations set up yet. Would you like to add one?"
    
    lines = ["📍 *Storage Locations*\n"]
    for loc in locations:
        name = loc.get("name", "Unknown")
        loc_type = loc.get("type", "")
        type_str = f" ({loc_type})" if loc_type else ""
        lines.append(f"• {name}{type_str}")
    
    return "\n".join(lines)


LOCATION_CREATED = "Created storage location '{name}'."

LOCATION_ERROR = "Couldn't create the location. Please try again."


# =============================================================================
# FILE PROCESSING
# =============================================================================

FILE_DETECTING = "Analyzing your file..."

FILE_DETECTED_PRICE_LIST = "This looks like a *price list*. Processing..."

FILE_DETECTED_INVOICE = "This looks like an *invoice*. Processing..."

FILE_DETECTION_UNSURE = (
    "What is this file?\n"
    "1. Supplier price list\n"
    "2. Invoice"
)

FILE_PROCESSING_STARTED = "Processing your {file_type}. I'll show you what I found."

FILE_PROCESSING_ERROR = "Couldn't process the file. Please make sure it's a clear image or PDF."


def file_preview_price_list(
    supplier_name: str,
    currency: str,
    items: list[dict[str, Any]],
) -> str:
    """Format price list preview."""
    lines = ["📋 *Price List Preview*\n"]
    lines.append(f"Supplier: {supplier_name or '⚠️ Not detected'}")
    lines.append(f"Currency: {currency or '⚠️ Not detected'}")
    lines.append(f"Items found: {len(items)}\n")
    
    for i, item in enumerate(items[:10], 1):
        name = item.get("supplier_name_raw", item.get("name", "Unknown"))
        price = item.get("price", "N/A")
        lines.append(f"{i}. {name} - {price}")
    
    if len(items) > 10:
        lines.append(f"...and {len(items) - 10} more items")
    
    lines.append("\nSay /confirm to save, or tell me what to change.")
    return "\n".join(lines)


def file_preview_invoice(
    supplier_name: str,
    invoice_number: str,
    total: str,
    currency: str,
    line_items: list[dict[str, Any]],
) -> str:
    """Format invoice preview."""
    lines = ["📄 *Invoice Preview*\n"]
    lines.append(f"Supplier: {supplier_name or '⚠️ Not detected'}")
    lines.append(f"Invoice #: {invoice_number or 'N/A'}")
    lines.append(f"Total: {total} {currency or ''}\n")
    
    lines.append("Line items:")
    for i, item in enumerate(line_items[:10], 1):
        desc = item.get("description_raw", item.get("description", "Unknown"))
        qty = item.get("quantity", "")
        price = item.get("unit_price", "")
        lines.append(f"{i}. {desc} - {qty} @ {price}")
    
    if len(line_items) > 10:
        lines.append(f"...and {len(line_items) - 10} more items")
    
    lines.append("\nSay /confirm to save, or tell me what to change.")
    return "\n".join(lines)


FILE_CONFIRMED = "Saved! {summary}"

FILE_CONFIRM_ERROR = "Couldn't save the data. Please try again."

FILE_MISSING_SUPPLIER = "What supplier is this from?"

FILE_MISSING_CURRENCY = "What currency is this in? (e.g., USD, EUR, INR)"

FILE_UPLOAD_PRICE_LIST_PROMPT = "Please upload the price list file for {supplier}."


# =============================================================================
# ERRORS
# =============================================================================

ERROR_GENERIC = "Something went wrong. Please try again."

ERROR_NOT_REGISTERED = "Please send /start to register first."

ERROR_NO_OUTLETS = "You don't have any outlets. Would you like to create one?"


# =============================================================================
# PROMPTS (asking for missing info)
# =============================================================================

PROMPT_OUTLET_NAME = "What would you like to name this outlet?"

PROMPT_SUPPLIER_NAME = "What's the supplier's name?"

PROMPT_WHICH_OUTLET = "Which outlet?"

PROMPT_STAFF_ROLE = "What role should they have? (staff or owner)"
