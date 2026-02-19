# Design: Price List Metadata Display

## Date
2026-02-19

## Context
User requested to show last updated date/time and uploader name in the `/prices` command output.

Current output:
```
💰 Prices — cheong hing

1. Aust Greek Style Yoghurt — 600.00 HKD/tub (updated Jun 25)
...
```

Desired output:
```
💰 Prices — cheong hing
Last updated: 12 Jun 2026, 5:35 AM
Updated by: Avishek

1. Aust Greek Style Yoghurt — 600.00 HKD/tub
...
```

## Data Model

Existing tables:
- `supplier_price_lists` – `created_at` timestamp when price list was uploaded
- `file_processing_staging` – `uploaded_by` (FK → users.id), `supplier_id`, `restaurant_id`, `status=confirmed`, `document_type='price_list'`
- `users` – `full_name`

No direct FK from `supplier_price_lists` to `file_processing_staging`, but we can query the most recent staging record for the same `restaurant_id + supplier_id` combination to get the uploader's name.

## Design Decisions

### 1. What timestamp to show?
- **Last updated**: Most recent `SupplierPriceList.created_at` for the given restaurant+supplier.
- Rationale: Price lists can only be updated via file upload; each upload creates a new price list record with its own `created_at`. The most recent `created_at` reflects the latest upload time.

### 2. How to get uploader name?
- Query `FileProcessingStaging` filtered by:
  - `restaurant_id` and `supplier_id` matching the price list
  - `status = 'confirmed'`
  - `document_type = 'price_list'`
  - ordered by `created_at DESC`, limit 1
- Join to `User` to get `full_name`

### 3. Edge Cases
- No price list exists → no timestamp displayed
- No confirmed staging record → no uploader name displayed
- Price list exists but staging record missing (e.g., imported via API) → show only timestamp
- Multiple price lists: we show the most recent one's timestamp
- Multiple staging records: we show the most recent confirmed one's uploader name

## Implementation

### New Service Method
`SupplierService.get_price_list_meta(restaurant_id, supplier_id) -> (last_updated: datetime | None, uploader_name: str | None)`

Performs two independent scalar queries:
1. `SELECT created_at FROM supplier_price_lists WHERE restaurant_id = ? AND supplier_id = ? ORDER BY created_at DESC LIMIT 1`
2. `SELECT u.full_name FROM file_processing_staging s JOIN users u ON s.uploaded_by = u.id WHERE s.restaurant_id = ? AND s.supplier_id = ? AND s.status = 'confirmed' AND s.document_type = 'price_list' ORDER BY s.created_at DESC LIMIT 1`

### Frontend Changes
In `app/telegram/handlers/commands.py`, `_render_prices_page()`:
- Call `svc.get_price_list_meta()`
- Add header lines:
  - `if last_updated: lines.append(f"Last updated: {last_updated.strftime('%-d %B %Y %-I:%M %p UTC')}")`
  - `if uploader_name: lines.append(f"Updated by: {uploader_name}")`
- Add blank line before items

## Testing

### Unit Tests
Added `TestGetPriceListMeta` class in `tests/test_supplier_service.py` covering:
- Both values present
- Only last_updated present
- Only uploader_name present
- Neither present

### Integration
Existing `/prices` command tests will need updating to accommodate new header lines.

## Files Changed
- `app/services/supplier_service.py` – new `get_price_list_meta()` method
- `app/telegram/handlers/commands.py` – updated `_render_prices_page()`
- `tests/test_supplier_service.py` – new test class

## Rollout
No database migration required. All data already exists. Feature is additive and backward compatible.

## Future Considerations
- If we add manual price editing later, `last_updated` may need to track `updated_at` column.
- Could add a foreign key from `supplier_price_lists` to `file_processing_staging` to guarantee referential integrity.