"""supplier owned + nullable run restaurant

Revision ID: 82ac4de67f8f
Revises: 6eed1d0b322e
Create Date: 2026-01-26 11:56:50.496410

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82ac4de67f8f'
down_revision: Union[str, Sequence[str], None] = '6eed1d0b322e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("suppliers", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_suppliers_user_id"), "suppliers", ["user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_suppliers_user_id_users"),
        "suppliers",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.alter_column(
        "file_processing_runs",
        "restaurant_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "file_processing_staging",
        "restaurant_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "file_processing_staging",
        "restaurant_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "file_processing_runs",
        "restaurant_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    op.drop_constraint(op.f("fk_suppliers_user_id_users"), "suppliers", type_="foreignkey")
    op.drop_index(op.f("ix_suppliers_user_id"), table_name="suppliers")
    op.drop_column("suppliers", "user_id")
