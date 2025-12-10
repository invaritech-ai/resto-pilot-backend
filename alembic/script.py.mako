"""${message}"""

revision = ${repr(revision_id)}
down_revision = ${repr(down_revision_id)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
