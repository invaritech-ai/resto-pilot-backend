"""invite codes string and drop user role

Revision ID: 8e2c6a9d7c11
Revises: 401552a7cdbf
Create Date: 2025-12-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8e2c6a9d7c11"
down_revision: Union[str, Sequence[str], None] = "401552a7cdbf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invite_codes",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Convert invite_codes.code from UUID -> short string.
    op.alter_column(
        "invite_codes",
        "code",
        existing_type=sa.UUID(),
        type_=sa.String(length=10),
        postgresql_using="substring(replace(code::text,'-',''),1,10)",
        existing_nullable=False,
    )

    # Narrow role to (owner, staff).
    invite_target_role = sa.Enum("owner", "staff", name="invite_target_role")
    invite_target_role.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "invite_codes",
        "role",
        existing_type=sa.Enum("owner", "manager", "staff", name="invite_code_role"),
        type_=invite_target_role,
        postgresql_using="(case when role='manager' then 'staff' else role end)::invite_target_role",
        existing_nullable=False,
        existing_server_default=sa.text("'staff'::invite_code_role"),
    )
    op.alter_column(
        "invite_codes",
        "role",
        server_default=sa.text("'staff'::invite_target_role"),
        existing_type=invite_target_role,
        existing_nullable=False,
    )
    sa.Enum(name="invite_code_role").drop(op.get_bind(), checkfirst=False)

    # No role is stored on user; roles are per-restaurant membership.
    op.drop_column("users", "role")


def downgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(length=50), nullable=True))

    invite_code_role = sa.Enum("owner", "manager", "staff", name="invite_code_role")
    invite_code_role.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "invite_codes",
        "role",
        existing_type=sa.Enum("owner", "staff", name="invite_target_role"),
        type_=invite_code_role,
        postgresql_using="role::text::invite_code_role",
        existing_nullable=False,
        existing_server_default=sa.text("'staff'::invite_target_role"),
    )
    op.alter_column(
        "invite_codes",
        "role",
        server_default=sa.text("'staff'::invite_code_role"),
        existing_type=invite_code_role,
        existing_nullable=False,
    )
    sa.Enum(name="invite_target_role").drop(op.get_bind(), checkfirst=False)

    op.alter_column(
        "invite_codes",
        "code",
        existing_type=sa.String(length=10),
        type_=sa.UUID(),
        postgresql_using="code::uuid",
        existing_nullable=False,
    )

    op.drop_column("invite_codes", "created_at")
