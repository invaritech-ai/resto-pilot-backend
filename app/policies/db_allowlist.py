from __future__ import annotations

from typing import Final

# Central allowlist for DB actions.
# Edit this file to adjust which roles can access which tables/columns.

ROLE_OWNER: Final[str] = "owner"
ROLE_STAFF: Final[str] = "staff"

SCOPE_SELF: Final[str] = "self"
SCOPE_OWNED_RESTAURANT: Final[str] = "owned_restaurant"
SCOPE_RESTAURANT_OWNER: Final[str] = "restaurant_owner"
SCOPE_RESTAURANT_MEMBER: Final[str] = (
    "restaurant_member"  # For staff viewing staff in restaurants they're members of
)

# Tables that are never exposed via the DB engine.
BUSINESS_TABLES_DENYLIST: Final[set[str]] = {
    "db_pending_actions",
    "telegram_sessions",
    "telegram_messages",
    "telegram_outgoing_messages",
    "telegram_chat_memory",
    "telegram_chat_states",
    "processing_events",
    "llm_calls",
}

# Structure:
# DB_ALLOWLIST[role][table][crud] = {"columns": [...], "scope": "<scope>"}
DB_ALLOWLIST: Final[dict[str, dict[str, dict[str, dict[str, object]]]]] = {
    ROLE_OWNER: {
        "users": {
            "read": {
                "columns": ["full_name", "username", "phone", "is_phone_verified"],
                "scope": SCOPE_SELF,
            },
            "update": {
                "columns": ["full_name", "username", "phone"],
                "scope": SCOPE_SELF,
            },
        },
        "restaurants": {
            "read": {
                "columns": [
                    "name",
                    "restaurant_code",
                    "onboarding_status",
                    "created_at",
                    "last_active_at",
                ],
                "scope": SCOPE_OWNED_RESTAURANT,
            },
            "update": {
                "columns": ["name", "onboarding_status"],
                "scope": SCOPE_OWNED_RESTAURANT,
            },
        },
        "restaurant_users": {
            "read": {
                "columns": [
                    "restaurant_id",
                    "user_id",
                    "role",
                    "status",
                    "joined_at",
                    "invited_by",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "user_id",
                    "role",
                    "invited_by",
                    "status",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["role", "status"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "delete": {
                "columns": [],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "invite_codes": {
            "read": {
                "columns": [
                    "code",
                    "restaurant_id",
                    "role",
                    "expires_at",
                    "used_at",
                    "created_by",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "create": {
                "columns": [
                    "code",
                    "restaurant_id",
                    "role",
                    "expires_at",
                    "created_by",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["expires_at"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "delete": {
                "columns": [],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
    },
    ROLE_STAFF: {
        "users": {
            "read": {
                "columns": ["full_name", "username", "phone", "is_phone_verified"],
                "scope": SCOPE_SELF,
            },
            "update": {
                "columns": ["full_name", "username", "phone"],
                "scope": SCOPE_SELF,
            },
        },
        "restaurant_users": {
            "read": {
                "columns": [
                    "restaurant_id",
                    "user_id",
                    "role",
                    "status",
                    "joined_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
    },
}
