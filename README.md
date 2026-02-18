# Resto Pilot Backend

Telegram-first backend for restaurant supplier, pricing, and inventory workflows.

## Current capabilities
- Onboarding and restaurant context selection.
- Supplier management:
  - `/list suppliers`
  - `/add supplier <name>`
  - `/link supplier <name>`
- Price catalog and inventory reads:
  - `/products`, `/prices <supplier>`, `/inventory`, `/balance`
- Upload pipeline for invoices and price lists:
  - file upload -> document type selection -> OCR -> review -> confirm
  - informative staged progress updates during OCR (step/status/elapsed/ETA)
  - supplier resolution options: choose existing / type name / create new
  - invoice item resolution gate before inventory writes
- Paginated list UX with inline buttons (latest list message only; stale buttons are rejected).
- Telemetry:
  - incoming message/session dedup tracking
  - outgoing message logging
  - OCR/parser LLM usage logging (tokens + model metadata)

## Architecture (high level)
1. `POST /api/v1/telegram` receives webhook updates.
2. Webhook sends instant ACK and enqueues `handle_telegram_update` Celery task.
3. Worker routes each update by priority (reset -> button -> command -> file -> pattern -> fallback).
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
- SQS/Celery notes: `docs/aws-sqs-celery-broker-notes.md`

## Notes
- Webhook secret must be set via `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN`.
- A running Celery worker is required for message processing and file OCR tasks.
