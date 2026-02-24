"""
User context service — read/write user.context JSONB.

user.context is transient UX state (not business data). Shape:
    {
        "active_restaurant_id": "uuid-str",   # which restaurant is active
        "last_list_type":       "suppliers",   # for interpreting "next"/"prev"
        "last_list_offset":     20,            # pagination offset
        "numbered_items":       ["uuid", ...], # for resolving "#2"
        "active_staging_id":    "uuid-str",    # upload currently being reviewed
    }

Rules:
- active_restaurant_id is preserved on reset (it's a preference, not navigation).
- All other fields are cleared on Global Reset (home/cancel/start).
- Callers own the commit — this service only flushes to keep within the caller's tx.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models.user import User

# Fields cleared by Global Reset (Priority 1).
_NAV_FIELDS = (
    "last_list_type",
    "last_list_offset",
    "numbered_items",
    "active_list_message_id",    # active paginated-list message id (invalidate old buttons)
    "active_staging_id",
    "pending_item_resolutions",  # step 8: per-item resolution state during upload confirm
    "review_message_id",         # step 8: Telegram message_id for edit-in-place review
    "editing_staging_id",        # step 8: staging_id being edited (item edit flow)
    "editing_item_idx",          # step 8: 0-based item index being edited
    "editing_field",             # step 8: field name being edited (name/qty/unit/price)
    "supplier_input_staging_id", # staging_id waiting for typed supplier input
    "supplier_input_mode",       # supplier input mode: resolve or create
    "prices_supplier_id",        # active supplier context for /prices pagination callbacks
    "adj_item_id",               # quick stock adj: UUID of matched inventory item
    "adj_item_name",             # quick stock adj: display name (also used for new-item creation)
    "adj_qty",                   # quick stock adj: quantity (float)
    "adj_unit",                  # quick stock adj: unit string (optional)
    "po_input_id",               # PO add-item mode: UUID of draft PO awaiting item text
)


class ContextService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, user: User) -> dict[str, Any]:
        """Return the full context dict (never None)."""
        return user.context or {}

    def get_active_restaurant_id(self, user: User) -> uuid.UUID | None:
        """Return active_restaurant_id as UUID, or None."""
        raw = self.get(user).get("active_restaurant_id")
        if raw is None:
            return None
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None

    def get_active_staging_id(self, user: User) -> uuid.UUID | None:
        """Return active_staging_id as UUID, or None."""
        raw = self.get(user).get("active_staging_id")
        if raw is None:
            return None
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None

    def get_numbered_items(self, user: User) -> list[uuid.UUID]:
        """Return numbered_items as a list of UUIDs (empty list if not set)."""
        raw = self.get(user).get("numbered_items", [])
        result: list[uuid.UUID] = []
        for item in raw:
            try:
                result.append(uuid.UUID(str(item)))
            except ValueError:
                pass
        return result

    def get_last_list_type(self, user: User) -> str | None:
        return self.get(user).get("last_list_type")

    def get_last_list_offset(self, user: User) -> int:
        return int(self.get(user).get("last_list_offset", 0))

    def get_fields(self, user: User) -> dict[str, Any]:
        """Compatibility alias for get().

        Some handlers call `get_fields()` when they need to read multiple context
        keys at once (for example, item-edit state fields). Keeping this wrapper
        avoids duplicate context-access patterns across call sites while preserving
        the existing `get()` behavior and return shape.
        """
        return self.get(user)

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def set_fields(self, user: User, **fields: Any) -> None:
        """Merge-update specific fields into user.context and flush.

        Values of None remove the key. Callers must commit.

        Examples:
            ctx.set_fields(user, active_staging_id=str(staging.id))
            ctx.set_fields(user, last_list_type="suppliers", last_list_offset=0)
            ctx.set_fields(user, active_staging_id=None)  # removes key
        """
        ctx: dict[str, Any] = dict(user.context or {})
        for key, value in fields.items():
            if value is None:
                ctx.pop(key, None)
            else:
                ctx[key] = value
        user.context = ctx
        flag_modified(user, "context")
        self.session.add(user)
        self.session.flush()

    def set_active_restaurant(self, user: User, restaurant_id: uuid.UUID | str) -> None:
        """Set the active restaurant for this user."""
        self.set_fields(user, active_restaurant_id=str(restaurant_id))

    def set_active_staging(self, user: User, staging_id: uuid.UUID | str) -> None:
        """Set the staging record currently being reviewed."""
        self.set_fields(user, active_staging_id=str(staging_id))

    def set_numbered_items(self, user: User, items: list[uuid.UUID]) -> None:
        """Store a numbered list for resolving '#2'-style references."""
        self.set_fields(user, numbered_items=[str(i) for i in items])

    def set_list_state(self, user: User, list_type: str, offset: int) -> None:
        """Update pagination state after showing a list."""
        self.set_fields(user, last_list_type=list_type, last_list_offset=offset)

    def clear_navigation(self, user: User) -> None:
        """Clear all navigation state (Global Reset). Preserves active_restaurant_id."""
        ctx: dict[str, Any] = dict(user.context or {})
        for field in _NAV_FIELDS:
            ctx.pop(field, None)
        user.context = ctx
        flag_modified(user, "context")
        self.session.add(user)
        self.session.flush()

    def clear_all(self, user: User) -> None:
        """Wipe the entire context (e.g. on logout or restaurant switch)."""
        user.context = {}
        flag_modified(user, "context")
        self.session.add(user)
        self.session.flush()
