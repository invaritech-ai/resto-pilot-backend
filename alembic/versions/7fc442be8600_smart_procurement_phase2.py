"""smart_procurement_phase2

Revision ID: 7fc442be8600
Revises: b7c2d3e4f5a6
Create Date: 2026-02-19 19:36:25.575391

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7fc442be8600'
down_revision: Union[str, Sequence[str], None] = 'b7c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add par levels and purchase order tables."""
    # purchase_orders must be created before purchase_order_items (FK dependency)
    op.create_table(
        'purchase_orders',
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('supplier_id', sa.Uuid(), nullable=True),
        sa.Column('supplier_name', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='draft', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'received', 'cancelled')",
            name='ck_purchase_orders_status',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name=op.f('fk_purchase_orders_created_by_users'),
                                ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'],
                                name=op.f('fk_purchase_orders_restaurant_id_restaurants'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'],
                                name=op.f('fk_purchase_orders_supplier_id_suppliers'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_orders')),
    )
    op.create_index(
        'ix_purchase_orders_restaurant_status', 'purchase_orders',
        ['restaurant_id', 'status'], unique=False,
    )

    op.create_table(
        'inventory_par_levels',
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('inventory_item_id', sa.Uuid(), nullable=False),
        sa.Column('par_qty', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name=op.f('fk_inventory_par_levels_created_by_users'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'],
                                name=op.f('fk_inventory_par_levels_inventory_item_id_inventory_items'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'],
                                name=op.f('fk_inventory_par_levels_restaurant_id_restaurants'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_par_levels')),
        sa.UniqueConstraint('restaurant_id', 'inventory_item_id',
                            name='uq_inventory_par_levels_restaurant_item'),
    )
    op.create_index('ix_par_levels_restaurant', 'inventory_par_levels', ['restaurant_id'], unique=False)

    op.create_table(
        'purchase_order_items',
        sa.Column('po_id', sa.Uuid(), nullable=False),
        sa.Column('inventory_item_id', sa.Uuid(), nullable=True),
        sa.Column('item_name', sa.String(length=200), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('unit_price_minor', sa.BigInteger(), nullable=True),
        sa.Column('unit_price_exp', sa.SmallInteger(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('received_qty', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'],
                                name=op.f('fk_purchase_order_items_inventory_item_id_inventory_items'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'],
                                name=op.f('fk_purchase_order_items_po_id_purchase_orders'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_order_items')),
    )
    op.create_index('ix_po_items_item', 'purchase_order_items', ['inventory_item_id'], unique=False)
    op.create_index('ix_po_items_po', 'purchase_order_items', ['po_id'], unique=False)
    # NOTE: GIN trigram indexes (idx_suppliers_name_trgm, idx_supplier_prices_name_trgm,
    # ix_inventory_items_name_trgm) are NOT modified here — they live in earlier migrations
    # and are not tracked by autogenerate (custom operator class).


def downgrade() -> None:
    """Downgrade schema — remove par levels and purchase order tables."""
    op.drop_index('ix_po_items_po', table_name='purchase_order_items')
    op.drop_index('ix_po_items_item', table_name='purchase_order_items')
    op.drop_table('purchase_order_items')
    op.drop_index('ix_par_levels_restaurant', table_name='inventory_par_levels')
    op.drop_table('inventory_par_levels')
    op.drop_index('ix_purchase_orders_restaurant_status', table_name='purchase_orders')
    op.drop_table('purchase_orders')
