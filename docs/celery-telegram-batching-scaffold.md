# Celery + Telegram batching scaffold

This project supports a “batched Telegram ingest” mode behind a feature flag.

## Environment variables
- `APP_TELEGRAM_BATCHING_ENABLED=true` to enable batching mode for Telegram sessions.
- `APP_TELEGRAM_BATCH_IDLE_SECONDS=30` inactivity debounce window.
- `APP_TELEGRAM_BATCH_MAX_SECONDS=180` hard cap window.
- `APP_CELERY_BROKER_URL=...` (Upstash is typically `rediss://...`, SQS is `sqs://`).
- `APP_CELERY_RESULT_BACKEND=` optional; leave empty unless you need task results (set to a real backend URL).

### If using AWS SQS
- Install: `celery[sqs]` (already included in this repo's dependencies).
- Set:
  - `APP_CELERY_BROKER_URL=sqs://`
  - `APP_CELERY_SQS_QUEUE_URL=...`
  - `APP_CELERY_SQS_REGION=...` (or rely on `AWS_DEFAULT_REGION`)

## What happens in batching mode
- Webhook validates the secret header, then enqueues `handle_telegram_update(update)` and returns `{"status":"ok"}` immediately.
- Celery worker runs `handle_update(...)` which persists `telegram_sessions` + `telegram_messages`.
- Worker schedules a delayed Celery task `flush_session(...)` at `flush_at`.
- `flush_session` no-ops if the session has newer activity; otherwise marks the session `processing` and enqueues `process_session`.
- `process_session` is currently a stub that writes `processing_events` with a v0 routing plan.

## Reserved commands
Some commands are treated as “instant” and bypass the normal 30s batching delay (currently: `/start`, `/respond`, `/done`). Even when batching is enabled, these commands are handled immediately by the worker.

## Run locally (example)
- Start Redis (local): `redis-server`
- Start API: `uv run uvicorn app.main:app --reload`
- Start worker: `uv run celery -A app.workers.celery_app.celery_app worker -l info`

## Reliability note (Redis eviction)
If your Redis provider enables eviction, queued Celery tasks can be dropped under memory pressure. For correctness, prefer a broker with eviction disabled (or use Postgres as the durable source of truth and re-drive “due sessions” periodically).
