# Testing Guide

## Test stack
- `pytest`
- unit + integration-style tests under `tests/`
- DB/session behavior tested with fixtures and mocks depending on module

## Run tests
```bash
# Full suite
uv run pytest

# Focused suites
uv run pytest tests/test_commands.py tests/test_buttons.py
uv run pytest tests/test_ocr_tasks.py tests/test_item_parser.py
uv run pytest tests/test_telegram_tasks.py tests/test_files_handler.py
uv run pytest tests/test_stock_parser.py
```

## Current baseline
- Full suite: 501 passed, 1 skipped (as of 2026-02-19)
- Keep full-suite green before pushing phase-completion commits

## Manual smoke checklist

### Setup
1. Start API + worker.
2. Send `/start`, complete onboarding (name → restaurant name → done).

### Core command verification
3. Run `/help` and verify all listed commands are usable.
4. `/profile` — shows name, restaurant, role.
5. `/outlets` — shows active restaurant.
6. `/balance` — shows stock summary with zero/negative sections.
7. `/inventory` — shows items with `[#N +]` `[#N -]` quick-adjust buttons.
8. `/chart` — sends PNG with per-unit bar panels.
9. `/export` — sends CSV file attachment.

### Upload flows
10. Upload invoice sample file:
    - Select "Invoice" doc type.
    - Validate OCR progress messages (step/status/elapsed/ETA).
    - Validate review, edit, pagination, supplier resolution (choose/type/create), and confirm flows.
    - Confirm → inventory rows written → `/balance` shows updated counts.
11. Upload price-list sample file:
    - Select "Price list" doc type.
    - Validate review and confirm flow.
    - Confirm → `/prices <supplier>` shows new entries.
12. Validate first-page header supplier recovery on a PDF where table/text extraction misses supplier.

### Search and history
13. `/search onion` — shows matching inventory items, supplier products, suppliers.
14. `/history onion` — shows last 10 transactions with ➕/➖ arrows and timestamps.
15. `/prices <supplier>` — shows prices (verify last-updated line and uploader name).

### Free-text stock shortcuts
16. Type `"used 1kg onion"` → confirm keyboard appears with balance delta → tap Confirm → balance updated.
17. Type `"2kg chicken left"` → set-balance confirm → tap Confirm → balance reconciled.
18. Type `"used 0 onion"` → no response (invalid, falls to NL).
19. Type `"onion 1kg"` (no keyword) → falls through to NL query (not parsed as stock phrase).

### Natural language queries
20. "how much onion do I have?" → bot answers from inventory context.
21. "where can I buy chicken?" → routes to `/search chicken` → deterministic results.
22. "show inventory" → routes to `/inventory` command.

### Quick-adjust buttons
23. `/inventory` → tap `[#1 +]` → toast confirms +1 → `/balance` shows updated count.
24. Tap `[#1 -]` until balance goes negative → ⚠️ Low stock alert shown.

### Supplier commands
25. `/products` — shows paginated product catalog.
26. `/list suppliers` — shows linked suppliers.
27. `/add supplier <new name>` — creates new supplier.

### Stale button rejection
28. Trigger a paginated list, navigate to page 2, then send a new `/inventory` — confirm old page-1 button returns graceful error (not crash).

## Regression areas to always include
- Supplier matching (`/prices`) with exact + partial names.
- Callback payload safety and handling of stale/invalid callback data.
- Inventory confirm path for duplicate item names and mixed resolutions.
- Telemetry side effects: outgoing message logs and LLM call usage rows.
- Free-text stock parser: `"used 1kg onion"` matches; `"onion 1kg"` does not.
- NL routing: supplier/product queries route to `/search`, not answered from empty context.
