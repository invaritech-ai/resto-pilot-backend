# Celery + Telegram batching scaffold

This project supports a “batched Telegram ingest” mode behind a feature flag.

## Environment variables
- `APP_TELEGRAM_BATCHING_ENABLED=true` to enable batching mode for the Telegram webhook.
- `APP_TELEGRAM_BATCH_IDLE_SECONDS=30` inactivity debounce window.
- `APP_TELEGRAM_BATCH_MAX_SECONDS=180` hard cap window.
- `APP_CELERY_BROKER_URL=...` (Upstash is typically `rediss://...`).
- `APP_CELERY_RESULT_BACKEND=` optional (can be empty).

## What happens in batching mode
- Webhook persists `telegram_sessions` + `telegram_messages`.
- Webhook schedules a delayed Celery task `flush_session(...)` at `flush_at`.
- `flush_session` no-ops if the session has newer activity; otherwise marks the session `processing` and enqueues `process_session`.
- `process_session` is currently a stub that writes `processing_events` with a v0 routing plan.

## Run locally (example)
- Start Redis (local): `redis-server`
- Start API: `uv run uvicorn app.main:app --reload`
- Start worker: `uv run celery -A app.workers.celery_app.celery_app worker -l info`

