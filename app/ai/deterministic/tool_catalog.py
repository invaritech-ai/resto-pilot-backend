from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    is_write: bool = False


def get_planner_tool_catalog() -> list[ToolSpec]:
    """
    Tool catalog for the deterministic planner.

    This catalog is intentionally decoupled from the current implementation in
    app/ai/db_tools/*. It reflects the *target* one-tool-call architecture:
    - Tools accept human-friendly queries (no IDs).
    - Tools are deterministic and validate/resolve entities internally.
    - Writes execute immediately (except file uploads).
    """
    return [
        ToolSpec(
            name="restaurants_list",
            description="List outlets you belong to (includes your role).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            is_write=False,
        ),
        ToolSpec(
            name="restaurants_create",
            description="Create a new outlet/restaurant you will own.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Outlet name to create."},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="restaurants_update",
            description="Rename/update an outlet. If restaurant_query is omitted, uses active outlet context. If ambiguous, the tool returns a numbered clarification payload.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {
                        "type": "string",
                        "description": "Outlet name or shortform (optional if active outlet is set).",
                    },
                    "name": {"type": "string", "description": "New outlet name."},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="staff_list",
            description="List staff members for an outlet. If restaurant_query is omitted, uses active outlet context; otherwise asks to clarify.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {
                        "type": "string",
                        "description": "Outlet name or shortform (optional if active outlet is set).",
                    },
                },
                "additionalProperties": False,
            },
            is_write=False,
        ),
        ToolSpec(
            name="invite_codes_create",
            description="Create an invite link for an outlet (owner-only). If restaurant_query is omitted, uses active outlet context; otherwise asks to clarify.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {
                        "type": "string",
                        "description": "Outlet name or shortform (optional if active outlet is set).",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["staff", "owner"],
                        "description": "Invitee role. Defaults to staff.",
                    },
                    "expires_in_days": {
                        "type": "integer",
                        "description": "Days until expiry (optional).",
                    },
                },
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="invite_codes_list",
            description="List invites for an outlet (owner-only). By default shows only active unused invites.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {
                        "type": "string",
                        "description": "Outlet name or shortform (optional if active outlet is set).",
                    },
                    "include_used": {
                        "type": "boolean",
                        "description": "Include used invite codes (optional).",
                    },
                    "include_expired": {
                        "type": "boolean",
                        "description": "Include expired invite codes (optional).",
                    },
                },
                "additionalProperties": False,
            },
            is_write=False,
        ),
        ToolSpec(
            name="invite_codes_move",
            description="Move an invite code to another outlet (owner-only).",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Invite code to move."},
                    "restaurant_query": {
                        "type": "string",
                        "description": "Target outlet name or shortform.",
                    },
                },
                "required": ["code", "restaurant_query"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="suppliers_list",
            description="List suppliers you can access. If restaurant_query is provided, scope to that outlet; otherwise list across all outlets.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {
                        "type": "string",
                        "description": "Optional outlet name to scope suppliers.",
                    }
                },
                "additionalProperties": False,
            },
            is_write=False,
        ),
        ToolSpec(
            name="restaurant_suppliers_link",
            description="Link an existing supplier to an outlet. If supplier is not found, do NOT offer manual creation; instruct user to upload a supplier price list to add it.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {"type": "string", "description": "Outlet name."},
                    "supplier_query": {"type": "string", "description": "Supplier name."},
                },
                "required": ["restaurant_query", "supplier_query"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="restaurant_suppliers_unlink",
            description="Unlink/disassociate a supplier from an outlet (owner-only). Supplier can be specified by name or by number from the last supplier list for that outlet.",
            parameters={
                "type": "object",
                "properties": {
                    "restaurant_query": {"type": "string", "description": "Outlet name."},
                    "supplier_query": {
                        "type": "string",
                        "description": "Supplier name (optional if supplier_ref provided).",
                    },
                    "supplier_ref": {
                        "type": "integer",
                        "description": "Number from the last presented supplier list (optional).",
                    },
                },
                "required": ["restaurant_query"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="supplier_items_search",
            description="Search supplier catalog items by keyword(s). If no outlet/supplier specified, search across all accessible outlets/suppliers.",
            parameters={
                "type": "object",
                "properties": {
                    "item_query": {"type": "string", "description": "Search query, e.g. 'pork belly'."},
                    "restaurant_query": {"type": "string", "description": "Optional outlet name to scope search."},
                    "supplier_query": {"type": "string", "description": "Optional supplier name to scope search."},
                    "limit": {"type": "integer", "description": "Page size (optional)."},
                    "cursor": {"type": "string", "description": "Opaque cursor for pagination (optional)."},
                },
                "required": ["item_query"],
                "additionalProperties": False,
            },
            is_write=False,
        ),
        ToolSpec(
            name="files_process",
            description="Start file processing (price list / invoice / inventory photo). Creates a staging record for review/confirm. This is the only write flow that requires confirmation.",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["price_list", "invoice", "inventory_photo"],
                        "description": "File kind.",
                    },
                    "restaurant_query": {"type": "string", "description": "Outlet name or shortform."},
                    "supplier_query": {
                        "type": "string",
                        "description": "Supplier name (optional; used for price list/invoice when known).",
                    },
                    "file_id": {"type": "string", "description": "Telegram file_id."},
                },
                "required": ["kind", "restaurant_query", "file_id"],
                "additionalProperties": False,
            },
            is_write=True,
        ),
        ToolSpec(
            name="help",
            description="Show help for a topic or show the main menu if topic is empty.",
            parameters={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Optional topic: profile, outlets, staff, suppliers, invites, invoices, inventory, files."}
                },
                "additionalProperties": False,
            },
            is_write=False,
        ),
    ]


def tool_catalog_as_planner_json() -> list[dict[str, Any]]:
    """JSON-serializable representation for PlannerLLM input."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "is_write": t.is_write,
        }
        for t in get_planner_tool_catalog()
    ]

