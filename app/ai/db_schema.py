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
from app.policies.db_allowlist import (
    DB_ALLOWLIST,
    SCOPE_OWNED_RESTAURANT,
    SCOPE_RESTAURANT_OWNER,
    SCOPE_SELF,
)


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


def get_table_schema(table_name: str, role: str) -> dict[str, Any]:
    """
    Returns table schema with columns, types, constraints, and permissions.

    Args:
        table_name: Name of the table
        role: User role (owner or staff)

    Returns:
        Dictionary with schema information including columns, types, nullable, constraints, and permissions
    """
    model = _TABLE_MODELS.get(table_name)
    if model is None:
        return {}

    # Get allowlist permissions for this role and table
    role_allowlist = DB_ALLOWLIST.get(role, {})
    table_permissions = role_allowlist.get(table_name, {})

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

        # Check if column is in allowlist for any CRUD operation
        allowed_for_crud = {}
        for crud, spec in table_permissions.items():
            allowed_columns = spec.get("columns", [])
            if column.name in allowed_columns:
                allowed_for_crud[crud] = spec.get("scope")
        col_info["allowed_for_crud"] = allowed_for_crud

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
        "permissions": table_permissions,
    }


def get_allowed_tables_for_role(role: str) -> dict[str, Any]:
    """
    Returns all tables accessible to a role with their CRUD permissions.

    Args:
        role: User role (owner or staff)

    Returns:
        Dictionary mapping table names to their schema and permissions
    """
    role_allowlist = DB_ALLOWLIST.get(role, {})
    result = {}

    for table_name in role_allowlist.keys():
        result[table_name] = get_table_schema(table_name, role)

    return result


def format_schema_for_llm(schema: dict[str, Any], role: str) -> str:
    """
    Formats schema info as a readable string for LLM prompts.

    Args:
        schema: Schema dictionary from get_allowed_tables_for_role()
        role: User role for context

    Returns:
        Formatted string describing tables, columns, and permissions
    """
    lines = []
    lines.append(f"Available database tables and columns for role '{role}':\n")

    for table_name, table_info in schema.items():
        lines.append(f"\n## Table: {table_name}")

        permissions = table_info.get("permissions", {})
        if not permissions:
            lines.append("  (No access)")
            continue

        # List CRUD operations available
        crud_ops = list(permissions.keys())
        lines.append(f"  Operations: {', '.join(crud_ops)}")

        # List columns with details
        lines.append("  Columns:")
        for col in table_info.get("columns", []):
            col_name = col["name"]
            col_type = col["type"]
            nullable = "optional" if col["nullable"] else "required"
            primary_key = " (PRIMARY KEY)" if col["primary_key"] else ""
            unique = " (UNIQUE)" if col.get("unique") else ""

            # Show which CRUD operations allow this column
            allowed_crud = col.get("allowed_for_crud", {})
            if allowed_crud:
                crud_list = ", ".join(allowed_crud.keys())
                scope_info = ""
                for crud, scope in allowed_crud.items():
                    if scope:
                        scope_info += f" (scope: {scope})"
                lines.append(
                    f"    - {col_name}: {col_type}, {nullable}{primary_key}{unique} [allowed for: {crud_list}{scope_info}]"
                )
            else:
                lines.append(
                    f"    - {col_name}: {col_type}, {nullable}{primary_key}{unique} [not accessible]"
                )

        # Show unique constraints
        unique_constraints = table_info.get("unique_constraints", [])
        if unique_constraints:
            lines.append("  Unique constraints:")
            for constraint in unique_constraints:
                lines.append(f"    - {', '.join(constraint)}")

        # Show foreign keys
        foreign_keys = table_info.get("foreign_keys", [])
        if foreign_keys:
            lines.append("  Foreign keys:")
            for fk in foreign_keys:
                lines.append(f"    - {fk['column']} → {fk['references']}")

        # Show scope restrictions per CRUD
        lines.append("  Scope restrictions:")
        for crud, spec in permissions.items():
            scope = spec.get("scope")
            allowed_cols = spec.get("columns", [])
            lines.append(f"    - {crud}: scope={scope}, columns={allowed_cols}")

    return "\n".join(lines)


def get_table_column_descriptions(role: str) -> str:
    """
    Get a concise description of tables and columns for LLM prompts.
    This is a more compact version focused on what the LLM needs to know.

    Args:
        role: User role (owner or staff)

    Returns:
        Formatted string with table and column descriptions
    """
    schema = get_allowed_tables_for_role(role)
    lines = []

    for table_name, table_info in schema.items():
        permissions = table_info.get("permissions", {})
        if not permissions:
            continue

        # Table description
        crud_ops = list(permissions.keys())
        lines.append(f"\n{table_name} (operations: {', '.join(crud_ops)}):")

        # Required vs optional columns for create operations
        if "create" in permissions:
            create_spec = permissions["create"]
            create_columns = create_spec.get("columns", [])
            required_cols = []
            optional_cols = []

            for col in table_info.get("columns", []):
                col_name = col["name"]
                if col_name not in create_columns:
                    continue
                if col["primary_key"]:
                    continue  # Skip auto-generated primary keys
                if col["nullable"]:
                    optional_cols.append(f"{col_name} ({col['type']})")
                else:
                    required_cols.append(f"{col_name} ({col['type']})")

            if required_cols:
                lines.append(f"  Required for create: {', '.join(required_cols)}")
            if optional_cols:
                lines.append(f"  Optional for create: {', '.join(optional_cols)}")

        # Columns available for read
        if "read" in permissions:
            read_spec = permissions["read"]
            read_columns = read_spec.get("columns", [])
            if read_columns:
                lines.append(f"  Readable columns: {', '.join(read_columns)}")

        # Columns available for update
        if "update" in permissions:
            update_spec = permissions["update"]
            update_columns = update_spec.get("columns", [])
            if update_columns:
                lines.append(f"  Updatable columns: {', '.join(update_columns)}")

        # Scope information
        for crud, spec in permissions.items():
            scope = spec.get("scope")
            if scope:
                scope_desc = {
                    SCOPE_SELF: "only the current user's own record",
                    SCOPE_OWNED_RESTAURANT: "only restaurants owned by the user",
                    SCOPE_RESTAURANT_OWNER: "only restaurants where user is owner",
                }.get(scope, scope)
                lines.append(f"  {crud} scope: {scope_desc}")

    return "\n".join(lines)
