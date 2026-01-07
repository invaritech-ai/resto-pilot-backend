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
                    "address",
                    "internal_name",
                    "onboarding_status",
                    "created_at",
                    "last_active_at",
                ],
                "scope": SCOPE_OWNED_RESTAURANT,
            },
            "update": {
                "columns": ["name", "address", "internal_name", "onboarding_status"],
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
        "products": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "name_en",
                    "name_local",
                    "category",
                    "sub_category",
                    "storage_type",
                    "default_unit",
                    "default_unit_size",
                    "is_active",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "name_en",
                    "name_local",
                    "category",
                    "sub_category",
                    "storage_type",
                    "default_unit",
                    "default_unit_size",
                    "is_active",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": [
                    "name_en",
                    "name_local",
                    "category",
                    "sub_category",
                    "storage_type",
                    "default_unit",
                    "default_unit_size",
                    "is_active",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "product_aliases": {
            "read": {
                "columns": ["id", "product_id", "supplier_id", "alias_text", "confidence", "created_at"],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": ["product_id", "supplier_id", "alias_text", "confidence"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "suppliers": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "name",
                    "language",
                    "currency",
                    "lead_time_days",
                    "notes",
                    "is_active",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "name",
                    "language",
                    "currency",
                    "lead_time_days",
                    "notes",
                    "is_active",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["name", "language", "currency", "lead_time_days", "notes", "is_active"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "supplier_items": {
            "read": {
                "columns": [
                    "id",
                    "supplier_id",
                    "product_id",
                    "supplier_sku",
                    "supplier_name_raw",
                    "pack_size_text",
                    "unit_basis",
                    "min_order_qty",
                    "status",
                    "source_document_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "supplier_id",
                    "product_id",
                    "supplier_sku",
                    "supplier_name_raw",
                    "pack_size_text",
                    "unit_basis",
                    "min_order_qty",
                    "status",
                    "source_document_id",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "supplier_prices": {
            "read": {
                "columns": [
                    "id",
                    "supplier_item_id",
                    "price",
                    "currency",
                    "price_type",
                    "valid_from",
                    "valid_to",
                    "min_qty",
                    "source_document_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "supplier_item_id",
                    "price",
                    "currency",
                    "price_type",
                    "valid_from",
                    "valid_to",
                    "min_qty",
                    "source_document_id",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "documents": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "supplier_id",
                    "doc_type",
                    "language",
                    "effective_date",
                    "file_url",
                    "notes",
                    "uploaded_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "supplier_id",
                    "doc_type",
                    "language",
                    "effective_date",
                    "file_url",
                    "notes",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "inventory_locations": {
            "read": {
                "columns": ["id", "restaurant_id", "name", "type", "created_at", "updated_at"],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": ["restaurant_id", "name", "type"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["name", "type"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "inventory_batches": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "product_id",
                    "supplier_id",
                    "invoice_line_item_id",
                    "quantity",
                    "unit",
                    "unit_cost",
                    "received_date",
                    "expiry_date",
                    "location_id",
                    "status",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "product_id",
                    "supplier_id",
                    "invoice_line_item_id",
                    "quantity",
                    "unit",
                    "unit_cost",
                    "received_date",
                    "expiry_date",
                    "location_id",
                    "status",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["quantity", "status"],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Staff can update for movements
            },
        },
        "invoices": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "supplier_id",
                    "invoice_number",
                    "invoice_date",
                    "due_date",
                    "currency",
                    "subtotal",
                    "tax",
                    "total",
                    "document_id",
                    "status",
                    "authorized_by_user_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "supplier_id",
                    "invoice_number",
                    "invoice_date",
                    "due_date",
                    "currency",
                    "subtotal",
                    "tax",
                    "total",
                    "document_id",
                    "status",
                    "authorized_by_user_id",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["status"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "invoice_line_items": {
            "read": {
                "columns": [
                    "id",
                    "invoice_id",
                    "supplier_id",
                    "supplier_item_id",
                    "product_id",
                    "description_raw",
                    "quantity",
                    "unit",
                    "unit_price",
                    "line_total",
                    "currency",
                    "tax_amount",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "invoice_id",
                    "supplier_id",
                    "supplier_item_id",
                    "product_id",
                    "description_raw",
                    "quantity",
                    "unit",
                    "unit_price",
                    "line_total",
                    "currency",
                    "tax_amount",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "price_comparisons": {
            "read": {
                "columns": [
                    "id",
                    "invoice_line_item_id",
                    "supplier_price_id",
                    "expected_price",
                    "actual_price",
                    "delta",
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "invoice_line_item_id",
                    "supplier_price_id",
                    "expected_price",
                    "actual_price",
                    "delta",
                    "status",
                ],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["status", "reviewed_by", "reviewed_at"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "inventory_movements": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "inventory_batch_id",
                    "movement_type",
                    "quantity",
                    "reason",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "inventory_batch_id",
                    "movement_type",
                    "quantity",
                    "reason",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Staff can record movements
            },
        },
        "supplier_disputes": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "invoice_id",
                    "reason",
                    "status",
                    "resolution_notes",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": ["restaurant_id", "invoice_id", "reason", "status"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
            "update": {
                "columns": ["status", "resolution_notes"],
                "scope": SCOPE_RESTAURANT_OWNER,
            },
        },
        "file_processing_staging": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "user_id",
                    "document_id",
                    "processing_type",
                    "extracted_data_json",
                    "product_alias_matches_json",
                    "status",
                    "authorized_by_user_id",
                    "confirmed_at",
                    "cancelled_at",
                    "expires_at",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "update": {
                "columns": [
                    "extracted_data_json",
                    "product_alias_matches_json",
                    "status",
                    "authorized_by_user_id",
                    "confirmed_at",
                    "cancelled_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Users can update their own staging records
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
        "products": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "name_en",
                    "name_local",
                    "category",
                    "sub_category",
                    "storage_type",
                    "default_unit",
                    "default_unit_size",
                    "is_active",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "product_aliases": {
            "read": {
                "columns": ["id", "product_id", "supplier_id", "alias_text", "confidence", "created_at"],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "suppliers": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "name",
                    "language",
                    "currency",
                    "lead_time_days",
                    "notes",
                    "is_active",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "supplier_items": {
            "read": {
                "columns": [
                    "id",
                    "supplier_id",
                    "product_id",
                    "supplier_sku",
                    "supplier_name_raw",
                    "pack_size_text",
                    "unit_basis",
                    "min_order_qty",
                    "status",
                    "source_document_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "supplier_prices": {
            "read": {
                "columns": [
                    "id",
                    "supplier_item_id",
                    "price",
                    "currency",
                    "price_type",
                    "valid_from",
                    "valid_to",
                    "min_qty",
                    "source_document_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "documents": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "supplier_id",
                    "doc_type",
                    "language",
                    "effective_date",
                    "file_url",
                    "notes",
                    "uploaded_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "inventory_locations": {
            "read": {
                "columns": ["id", "restaurant_id", "name", "type", "created_at", "updated_at"],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "inventory_batches": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "product_id",
                    "supplier_id",
                    "invoice_line_item_id",
                    "quantity",
                    "unit",
                    "unit_cost",
                    "received_date",
                    "expiry_date",
                    "location_id",
                    "status",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "update": {
                "columns": ["quantity", "status"],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Staff can update for movements
            },
        },
        "invoices": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "supplier_id",
                    "invoice_number",
                    "invoice_date",
                    "due_date",
                    "currency",
                    "subtotal",
                    "tax",
                    "total",
                    "document_id",
                    "status",
                    "authorized_by_user_id",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "invoice_line_items": {
            "read": {
                "columns": [
                    "id",
                    "invoice_id",
                    "supplier_id",
                    "supplier_item_id",
                    "product_id",
                    "description_raw",
                    "quantity",
                    "unit",
                    "unit_price",
                    "line_total",
                    "currency",
                    "tax_amount",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "price_comparisons": {
            "read": {
                "columns": [
                    "id",
                    "invoice_line_item_id",
                    "supplier_price_id",
                    "expected_price",
                    "actual_price",
                    "delta",
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "inventory_movements": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "inventory_batch_id",
                    "movement_type",
                    "quantity",
                    "reason",
                    "created_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "create": {
                "columns": [
                    "restaurant_id",
                    "inventory_batch_id",
                    "movement_type",
                    "quantity",
                    "reason",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Staff can record movements
            },
        },
        "supplier_disputes": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "invoice_id",
                    "reason",
                    "status",
                    "resolution_notes",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
        },
        "file_processing_staging": {
            "read": {
                "columns": [
                    "id",
                    "restaurant_id",
                    "user_id",
                    "document_id",
                    "processing_type",
                    "extracted_data_json",
                    "product_alias_matches_json",
                    "status",
                    "authorized_by_user_id",
                    "confirmed_at",
                    "cancelled_at",
                    "expires_at",
                    "created_at",
                    "updated_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,
            },
            "update": {
                "columns": [
                    "extracted_data_json",
                    "product_alias_matches_json",
                    "status",
                    "authorized_by_user_id",
                    "confirmed_at",
                    "cancelled_at",
                ],
                "scope": SCOPE_RESTAURANT_MEMBER,  # Users can update their own staging records
            },
        },
    },
}
