"""inventory_schema_phase_1_step_7

Revision ID: a3f1b2c4d5e6
Revises: 0abc3a5619c7
Create Date: 2026-02-18 12:00:00.000000

Changes:
- CREATE TABLE inventory_items
- CREATE TABLE inventory_transactions
- CREATE TABLE inventory_balances
- ALTER TABLE file_processing_staging ADD COLUMN document_type
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3f1b2c4d5e6"
down_revision: Union[str, Sequence[str], None] = "0abc3a5619c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- inventory_items ---
    op.create_table(
        "inventory_items",
        sa.Column("restaurant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_lower", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            name=op.f("fk_inventory_items_restaurant_id_restaurants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_inventory_items_supplier_id_suppliers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_items")),
        sa.UniqueConstraint(
            "restaurant_id",
            "name_lower",
            name="uq_inventory_items_restaurant_name",
        ),
    )
    op.create_index(
        "ix_inventory_items_restaurant", "inventory_items", ["restaurant_id"], unique=False
    )
    op.execute(
        "CREATE INDEX ix_inventory_items_name_trgm "
        "ON inventory_items USING GIN(name_lower gin_trgm_ops)"
    )

    # --- inventory_transactions ---
    op.create_table(
        "inventory_transactions",
        sa.Column("restaurant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("txn_type", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("staging_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            name=op.f("fk_inventory_transactions_restaurant_id_restaurants"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["inventory_items.id"],
            name=op.f("fk_inventory_transactions_item_id_inventory_items"),
        ),
        sa.ForeignKeyConstraint(
            ["staging_id"],
            ["file_processing_staging.id"],
            name=op.f("fk_inventory_transactions_staging_id_file_processing_staging"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_inventory_transactions_created_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_transactions")),
    )
    op.create_index(
        "ix_inv_txn_restaurant_item",
        "inventory_transactions",
        ["restaurant_id", "item_id"],
        unique=False,
    )
    op.create_index(
        "ix_inv_txn_staging",
        "inventory_transactions",
        ["staging_id"],
        unique=False,
    )

    # --- inventory_balances ---
    op.create_table(
        "inventory_balances",
        sa.Column("restaurant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.Numeric(12, 3), server_default="0", nullable=False),
        sa.Column("last_txn_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            name=op.f("fk_inventory_balances_restaurant_id_restaurants"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["inventory_items.id"],
            name=op.f("fk_inventory_balances_item_id_inventory_items"),
        ),
        sa.ForeignKeyConstraint(
            ["last_txn_id"],
            ["inventory_transactions.id"],
            name=op.f("fk_inventory_balances_last_txn_id_inventory_transactions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_balances")),
        sa.UniqueConstraint(
            "restaurant_id",
            "item_id",
            name="uq_inventory_balances_restaurant_item",
        ),
    )

    # --- file_processing_staging: add document_type ---
    op.add_column(
        "file_processing_staging",
        sa.Column("document_type", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("file_processing_staging", "document_type")
    op.drop_table("inventory_balances")
    op.drop_index("ix_inv_txn_staging", table_name="inventory_transactions")
    op.drop_index("ix_inv_txn_restaurant_item", table_name="inventory_transactions")
    op.drop_table("inventory_transactions")
    op.execute("DROP INDEX IF EXISTS ix_inventory_items_name_trgm")
    op.drop_index("ix_inventory_items_restaurant", table_name="inventory_items")
    op.drop_table("inventory_items")
