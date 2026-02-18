# Flow and State Model

## Persistent state (database)
Primary source of truth lives in Postgres.

### `file_processing_staging`
Tracks file-ingestion lifecycle:
- `processing`
- `pending_review`
- `confirmed`
- `cancelled`
- `error`

Stores:
- `document_type`
- `supplier_id` (when resolved)
- `extracted_data_json`
- `session_id` (for telemetry threading)

### Inventory write path
Invoice confirmation writes:
- `inventory_transactions` (ledger)
- `inventory_balances` (current stock, updated atomically)

### Pricing write path
Price-list confirmation writes:
- `supplier_price_lists`
- `supplier_prices`

### Telemetry tables
- `telegram_messages` (incoming dedup/audit)
- `telegram_outgoing_messages` (outgoing bot messages)
- `llm_calls` (OCR/parser usage metadata)

## Transient state (`users.context`)
User UX/navigation state is stored in `users.context`.

Common fields:
- `active_restaurant_id`
- `active_staging_id`
- `review_message_id`
- `pending_item_resolutions`
- `editing_staging_id`
- `editing_item_idx`
- `editing_field`
- `supplier_input_staging_id`
- `supplier_input_mode` (`resolve` or `create`)
- `prices_supplier_id`
- `last_list_type`
- `last_list_offset`
- `numbered_items`
- `active_list_message_id`

## Upload flow summary
1. File arrives -> staging row created (`processing`).
2. User picks document type (`invoice` or `price_list`).
3. Processing message is edited with OCR progress stages (step/status/elapsed/ETA).
4. OCR/parser task extracts structured JSON.
5. If PDF text/tables do not yield a supplier, first-page vision header fallback attempts supplier extraction.
6. Supplier gate resolves automatically or presents choose/type/create options.
7. Review message sent (`pending_review`).
8. User edits/resolves items and confirms.
9. Final writes happen, staging set to `confirmed`.

## Pagination behavior
Command list pagination is context-stable:
- newest list message keeps active keyboard
- previous list keyboard is disabled
- stale callback taps are rejected with "list expired"
