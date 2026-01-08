"""
Database schema utilities for LLM prompts.

Provides table schema information for file processing and data extraction.
"""

from __future__ import annotations

from typing import Any

from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.db.models.products import Products
from app.db.models.product_aliases import ProductAliases
from app.db.models.suppliers import Suppliers
from app.db.models.supplier_items import SupplierItems
from app.db.models.supplier_prices import SupplierPrices
from app.db.models.documents import Documents
from app.db.models.inventory_locations import InventoryLocations
from app.db.models.inventory_batches import InventoryBatches
from app.db.models.invoices import Invoices
from app.db.models.invoice_line_items import InvoiceLineItems
from app.db.models.price_comparisons import PriceComparisons
from app.db.models.inventory_movements import InventoryMovements
from app.db.models.supplier_disputes import SupplierDisputes
from app.db.models.file_processing_staging import FileProcessingStaging


# Map table names to their SQLAlchemy models
_TABLE_MODELS = {
    "users": User,
    "restaurants": Restaurant,
    "restaurant_users": RestaurantUser,
    "invite_codes": InviteCodes,
    "products": Products,
    "product_aliases": ProductAliases,
    "suppliers": Suppliers,
    "supplier_items": SupplierItems,
    "supplier_prices": SupplierPrices,
    "documents": Documents,
    "inventory_locations": InventoryLocations,
    "inventory_batches": InventoryBatches,
    "invoices": Invoices,
    "invoice_line_items": InvoiceLineItems,
    "price_comparisons": PriceComparisons,
    "inventory_movements": InventoryMovements,
    "supplier_disputes": SupplierDisputes,
    "file_processing_staging": FileProcessingStaging,
}


def get_table_schema(table_name: str, role: str = "owner") -> dict[str, Any]:
    """
    Returns table schema with columns, types, and constraints.

    Args:
        table_name: Name of the table
        role: User role (unused, kept for compatibility)

    Returns:
        Dictionary with schema information including columns, types, nullable, and constraints
    """
    model = _TABLE_MODELS.get(table_name)
    if model is None:
        return {}

    # Extract column information from SQLAlchemy model
    columns_info = []
    for column in model.__table__.columns:
        col_info = {
            "name": column.name,
            "type": str(column.type),
            "nullable": column.nullable,
            "primary_key": column.primary_key,
            "unique": column.unique if hasattr(column, "unique") else False,
        }
        columns_info.append(col_info)

    # Extract unique constraints
    unique_constraints = []
    if hasattr(model.__table__, "unique_constraints"):
        for constraint in model.__table__.unique_constraints:
            if hasattr(constraint, "columns"):
                unique_constraints.append([col.name for col in constraint.columns])

    # Extract foreign keys
    foreign_keys = []
    for fk in model.__table__.foreign_keys:
        foreign_keys.append(
            {
                "column": fk.parent.name,
                "references": f"{fk.column.table.name}.{fk.column.name}",
            }
        )

    return {
        "table_name": table_name,
        "columns": columns_info,
        "unique_constraints": unique_constraints,
        "foreign_keys": foreign_keys,
    }
