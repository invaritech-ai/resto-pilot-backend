"""
Intent definitions for the static bot.

Contains the Intent enum used throughout the application.
Intent resolution is now handled by intent_resolver.py.
"""

from __future__ import annotations

from enum import Enum


class Intent(str, Enum):
    """Supported intents for the static bot."""

    # Profile
    VIEW_PROFILE = "view_profile"
    UPDATE_NAME = "update_name"
    UPDATE_PHONE = "update_phone"

    # Outlet (Restaurant)
    LIST_OUTLETS = "list_outlets"
    ADD_OUTLET = "add_outlet"
    UPDATE_OUTLET = "update_outlet"
    LIST_STAFF = "list_staff"
    ADD_STAFF = "add_staff"
    REVOKE_STAFF = "revoke_staff"

    # Supplier
    LIST_SUPPLIERS = "list_suppliers"
    ADD_SUPPLIER = "add_supplier"
    UPDATE_SUPPLIER = "update_supplier"
    VIEW_SUPPLIER = "view_supplier"
    VIEW_SUPPLIER_PRICE_LIST = "view_supplier_price_list"
    VIEW_SUPPLIER_ITEMS = "view_supplier_items"
    DEACTIVATE_SUPPLIER = "deactivate_supplier"

    # Invites
    LIST_INVITES = "list_invites"
    MOVE_INVITE = "move_invite"

    # Inventory
    LIST_INVENTORY = "list_inventory"
    ADD_INVENTORY = "add_inventory"
    UPDATE_INVENTORY = "update_inventory"
    LOG_INVENTORY_USAGE = "log_inventory_usage"
    LIST_LOCATIONS = "list_locations"
    ADD_LOCATION = "add_location"

    # Invoices
    LIST_INVOICES = "list_invoices"
    VIEW_INVOICE = "view_invoice"

    # File Upload
    UPLOAD_PRICE_LIST = "upload_price_list"
    UPLOAD_INVOICE = "upload_invoice"
    CONFIRM_UPLOAD = "confirm_upload"

    # Navigation
    SHOW_MENU = "show_menu"
    CANCEL = "cancel"
    HELP = "help"

    # Unknown / Can't help
    UNKNOWN = "unknown"
