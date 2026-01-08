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
📦 *Suppliers* - Manage your vendors
📊 *Inventory* - Track your stock
📄 *Files* - Upload price lists or invoices

Just tell me what you'd like to do!"""

CANT_HELP = "I can't help with that. Type /menu to see what I can do."

CANCEL_SUCCESS = "Cancelled. Type /menu to see available options."

CANCEL_NOTHING = "Nothing to cancel. Type /menu to see available options."


# =============================================================================
# ACKNOWLEDGMENTS
# =============================================================================

ACK_PROCESSING = "Got it..."

ACK_FILE_PROCESSING = "Processing your file..."


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

def staff_list(members: list[dict[str, Any]]) -> str:
    """Format staff list response."""
    if not members:
        return "No staff members yet. Would you like to invite someone?"
    
    lines = ["👥 *Staff Members*\n"]
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

SUPPLIER_UPDATED = "Updated supplier '{name}'."

SUPPLIER_CREATE_ERROR = "Couldn't add the supplier. Please try again."

SUPPLIER_NOT_FOUND = "Supplier not found."


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


# =============================================================================
# FILE PROCESSING
# =============================================================================

FILE_DETECTING = "Analyzing your file..."

FILE_DETECTED_PRICE_LIST = "This looks like a *price list*. Processing..."

FILE_DETECTED_INVOICE = "This looks like an *invoice*. Processing..."

FILE_DETECTION_UNSURE = "I'm not sure what type of file this is. Is it a price list or an invoice?"

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
