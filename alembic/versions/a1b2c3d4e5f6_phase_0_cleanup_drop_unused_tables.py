"""phase_0_cleanup: drop unused tables and columns

Revision ID: a1b2c3d4e5f6
Revises: 05dea9ec3e7a
Create Date: 2026-02-17

Drops tables removed in Phase 0 cleanup:
- invite_codes, product_aliases, supplier_prices, supplier_disputes
- price_comparisons, supplier_item_products
- file_processing_page_jobs, file_processing_steps, file_processing_payloads
- processing_events, user_upload_limits

Drops columns removed from existing tables:
- users: is_phone_verified, state dropped; state_data renamed to context
- restaurants: onboarding_status, address, internal_name
- suppliers: name_normalized, contact_email, contact_phone, language, lead_time_days
- restaurant_users: role, status, invited_by → replaced by is_owner, is_active booleans
- telegram_sessions: flush_at, hint_command
- inventory_batches: expiry_date, location_id, status
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "05dea9ec3e7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FK-dependent tables first, then parent tables

    # file_processing sub-tables
    op.drop_index("ix_file_processing_page_jobs_run_id", table_name="file_processing_page_jobs", if_exists=True)
    op.drop_index("ix_file_processing_page_jobs_page_index", table_name="file_processing_page_jobs", if_exists=True)
    op.drop_table("file_processing_page_jobs")

    op.drop_index("ix_file_processing_steps_run_id", table_name="file_processing_steps", if_exists=True)
    op.drop_table("file_processing_steps")

    op.drop_index("ix_file_processing_payloads_run_id", table_name="file_processing_payloads", if_exists=True)
    op.drop_index("ix_file_processing_payloads_page_index", table_name="file_processing_payloads", if_exists=True)
    op.drop_table("file_processing_payloads")

    # supplier sub-tables
    op.drop_index("ix_supplier_item_products_product_id", table_name="supplier_item_products", if_exists=True)
    op.drop_index("ix_supplier_item_products_restaurant_id", table_name="supplier_item_products", if_exists=True)
    op.drop_index("ix_supplier_item_products_supplier_item_id", table_name="supplier_item_products", if_exists=True)
    op.drop_table("supplier_item_products")

    op.drop_table("supplier_prices")
    op.drop_table("supplier_disputes")
    op.drop_table("price_comparisons")

    # standalone tables
    op.drop_table("invite_codes")
    op.drop_table("product_aliases")
    op.drop_index("ix_processing_events_session_id_at", table_name="processing_events", if_exists=True)
    op.drop_table("processing_events")
    op.drop_table("user_upload_limits")

    # Drop columns removed from users; rename state_data -> context (JSONB)
    op.drop_column("users", "is_phone_verified")
    op.drop_column("users", "state")
    op.alter_column("users", "state_data", new_column_name="context")

    # Drop columns removed from restaurants
    op.drop_column("restaurants", "onboarding_status")
    op.drop_column("restaurants", "address")
    op.drop_column("restaurants", "internal_name")

    # Drop columns removed from suppliers
    op.drop_index("ix_suppliers_name_normalized", table_name="suppliers", if_exists=True)
    op.drop_column("suppliers", "name_normalized")
    op.drop_column("suppliers", "contact_email")
    op.drop_column("suppliers", "contact_phone")
    op.drop_column("suppliers", "language")
    op.drop_column("suppliers", "lead_time_days")

    # Drop columns removed from restaurant_users (replace role/status enums with booleans)
    op.drop_column("restaurant_users", "invited_by")
    op.drop_column("restaurant_users", "status")
    op.drop_column("restaurant_users", "role")
    op.add_column("restaurant_users", sa.Column("is_owner", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("restaurant_users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))

    # Drop columns removed from telegram_sessions
    op.drop_index("ix_telegram_sessions_status_flush_at", table_name="telegram_sessions", if_exists=True)
    op.drop_column("telegram_sessions", "flush_at")
    op.drop_column("telegram_sessions", "hint_command")

    # Drop columns removed from inventory_batches
    op.drop_column("inventory_batches", "expiry_date")
    op.drop_column("inventory_batches", "location_id")
    op.drop_column("inventory_batches", "status")


def downgrade() -> None:
    # Tables and columns were removed intentionally - no downgrade
    pass
