"""make documents restaurant_id nullable for outlet independent price lists

Revision ID: 05dea9ec3e7a
Revises: 82ac4de67f8f
Create Date: 2026-02-12 01:54:48.053081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05dea9ec3e7a'
down_revision: Union[str, Sequence[str], None] = '82ac4de67f8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    # Make restaurant_id nullable to support outlet-independent price lists
    op.alter_column('documents', 'restaurant_id',
                    existing_type=sa.UUID(),
                    nullable=True)

def downgrade() -> None:
    """Downgrade schema."""
    # Make restaurant_id required again
    op.alter_column('documents', 'restaurant_id',
                    existing_type=sa.UUID(),
                    nullable=False)
