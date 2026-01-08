# Architecture Changes: Tool-First Agent + Operations Schema

## Summary

The platform moved from capability-gated replies to a tool-first agent with
policy enforcement, and expanded the schema to cover restaurant operations
(products, suppliers, invoices, inventory, and file processing).

## What Changed

### Removed or de-emphasized

- Capability-gated routing as the primary decision layer
- DBAction-only flows for user-facing operations (kept for legacy/optional paths)

### Added components

- `app/ai/agent.py` - General-purpose agent loop with tool-calling
- `app/ai/db_tools/` - Modular tool packages:
  - `profile.py`, `restaurants.py`, `staff.py`, `invites.py`
  - `products.py`, `suppliers.py`, `inventory.py`, `product_aliases.py`
  - `file_processing.py` (invoice/price list/inventory photo workflows)
- `app/ai/vision_client.py` - Vision model calls for document/image extraction
- `app/processing/file_processor.py` - Extraction + product alias matching
- Operations schema (new models + migration):
  - `products`, `suppliers`, `supplier_items`, `supplier_prices`
  - `documents`, `invoices`, `invoice_line_items`
  - `inventory_locations`, `inventory_batches`, `inventory_movements`
  - `product_aliases`, `price_comparisons`, `supplier_disputes`
  - `file_processing_staging`

### Updated components

- `app/processing/session_processor.py` - Runs the agent loop with tool-calling
- `app/policies/db_allowlist.py` - Expanded allowlist with restaurant-member scope
- `app/core/config.py` - Vision model configuration (`APP_VISION_*`)

## Key Improvements

1. **Tool-first behavior**: The LLM attempts tools (or asks for missing info)
   before refusing; denials come from policy checks inside tools.
2. **Operations schema**: Inventory, supplier, and invoice data are first-class.
3. **File processing pipeline**: Invoices, price lists, and inventory photos
   are extracted into a staging table for human review/confirm.
4. **Policy-based control**: Central allowlist governs role/scope access while
   tools enforce rules at execution time.
5. **Memory as context**: Conversation memory helps with continuity but never
   blocks tool calls; permissions are enforced by tools/policies.

## Migration guide

- **Add a new domain tool**:
  1. Create a module in `app/ai/db_tools/`
  2. Add permissions in `app/policies/db_allowlist.py`
  3. Register in `app/ai/db_tools/base.py`
- **Add a new operational table**:
  1. Add SQLAlchemy model in `app/db/models/`
  2. Add Alembic migration
  3. Update allowlist scopes/columns
  4. Add tools or processing tasks

## Docs updated

- `docs/capabilities.md` - Current tool-first architecture
- `docs/db-tools-patterns.md` - Tool patterns + policy usage
- `docs/resto-pilot-codebase-summary.md` - Expanded schema + file processing
- `docs/telegram-file-routing-and-batching.md` - File processing pipeline

## Date

January 2026
