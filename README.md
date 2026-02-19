# Resto Pilot Backend

Telegram-first backend for restaurant supplier, pricing, and inventory workflows.

## Current capabilities

### Inventory management
- `/inventory` — Stock levels with `[+]`/`[-]` ±1 quick-adjust buttons per row
- `/balance` — Stock summary with zero-stock and negative-balance alerts
- `/chart` — Visual per-unit-group bar chart (seaborn/PNG)
- `/history <item>` — Last 10 transactions for any item
- `/export` — Download full inventory as CSV
- Free-text stock shortcuts (no confirm UI friction):
  - `"used 1kg onion"` → debit with confirm keyboard + balance preview
  - `"2kg chicken left"` → set-balance reconcile with confirm keyboard

### Supplier & pricing
- `/list suppliers`, `/add supplier <name>`, `/link supplier <name>`
- `/products` — Paginated supplier product catalog
- `/prices <supplier>` — Prices from a supplier (includes last-updated and uploader)

### Search
- `/search <query>` — Unified fuzzy search across inventory items, supplier products, and supplier names

### Upload pipeline
- File upload → document type selection (invoice or price list) → OCR → review → confirm
- Informative staged progress updates during OCR (step/status/elapsed/ETA)
- Supplier resolution options: choose existing / type name / create new
- Invoice item resolution gate (fuzzy match ≥0.8 auto-links; 0.5–0.8 suggests; <0.5 creates new) before inventory writes
- Price list confirmation writes supplier prices with effective date tracking

### Natural language queries
- "how much onion do I have?" → answers from inventory context
- "where can I buy X?" → routes to `/search X`
- Full context injection: restaurant info, inventory summary, supplier list, live price and item matches

### UX & reliability
- Paginated list UX with inline buttons (latest list message only; stale buttons are rejected)
- Low-stock `⚠️` alerts after debit confirms

### Telemetry
- Incoming message/session dedup tracking
- Outgoing message logging
- OCR/parser LLM usage logging (tokens + model metadata)

## Architecture (high level)
1. `POST /api/v1/telegram` receives webhook updates.
2. Webhook sends instant ACK and enqueues `handle_telegram_update` Celery task.
3. Worker routes each update by priority: reset → button → command → file → stock phrase → NL query → fallback.
4. Upload OCR and parsing run in `process_file_task`.
5. Confirm actions write to final tables (`supplier_prices` and/or inventory ledger/balance).

## Local development
### 1) Setup
```bash
cp .env.example .env
# fill required APP_* vars
```

### 2) Database
```bash
uv run alembic upgrade head
```

### 3) Run services
```bash
./scripts/run_api.sh
./scripts/run_worker.sh
# or both:
./scripts/run_dev.sh
```

### 4) Run tests
```bash
uv run pytest
```

## Telegram commands
See `docs/telegram-bot-commands.md` for the up-to-date command list and BotFather menu guidance.

## Documentation index
- Product roadmap: `docs/ROADMAP.md`
- Router behavior: `docs/router_spec.md`
- Flow/state model: `docs/flow_states.md`
- Button callback schema: `docs/button_schema.md`
- OCR/parser contract: `docs/llm_contract.md`
- Testing guide: `docs/testing.md`
- Security report: `docs/security_best_practices_report.md`
- SQS/Celery notes: `docs/aws-sqs-celery-broker-notes.md`

## Notes
- Webhook secret must be set via `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN`.
- A running Celery worker is required for message processing and file OCR tasks.
