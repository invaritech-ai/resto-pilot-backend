"""add_check_constraints_step_7

Revision ID: b7c2d3e4f5a6
Revises: a3f1b2c4d5e6
Create Date: 2026-02-18 14:00:00.000000

Changes:
- ADD CONSTRAINT ck_inv_txn_type ON inventory_transactions (txn_type IN ('credit','debit'))
- ADD CONSTRAINT ck_inv_txn_source ON inventory_transactions (source IN ('invoice','manual'))
- ADD CONSTRAINT ck_staging_document_type ON file_processing_staging
  (document_type IS NULL OR document_type IN ('invoice','price_list'))
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b7c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a3f1b2c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_inv_txn_type",
        "inventory_transactions",
        "txn_type IN ('credit', 'debit')",
    )
    op.create_check_constraint(
        "ck_inv_txn_source",
        "inventory_transactions",
        "source IN ('invoice', 'manual')",
    )
    op.create_check_constraint(
        "ck_staging_document_type",
        "file_processing_staging",
        "document_type IS NULL OR document_type IN ('invoice', 'price_list')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_document_type", "file_processing_staging")
    op.drop_constraint("ck_inv_txn_source", "inventory_transactions")
    op.drop_constraint("ck_inv_txn_type", "inventory_transactions")
