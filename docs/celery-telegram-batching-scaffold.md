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
- Webhook validates the secret header, persists the update into Postgres (`telegram_sessions` + `telegram_messages`), may enqueue an immediate per-message backchannel (`send_message_backchannel(...)`), schedules a delayed Celery task `flush_session(...)` at `flush_at`, then returns `{"status":"ok"}`.
- `flush_session` no-ops if the session has newer activity; otherwise marks the session `processing` and enqueues two tasks:
  - `send_session_ack(session_id)` (best-effort short backchannel ack; may be skipped for naturalness)
  - `process_session(session_id)`
- `process_session` runs a cheap on-topic gate and then generates a single assistant reply.

## Reserved commands
Some commands bypass the normal 30s batching delay (currently: `/start`, `/respond`, `/done`).
- `/start` is delegated to Celery to handle registration/invites and to send responses.
- `/respond` and `/done` seal the current open session (`status=open -> processing`) and enqueue `send_session_ack` + `process_session`.

## Run locally (example)
- Start Redis (local): `redis-server`
- Start API: `./scripts/run_api.sh` (or `uv run uvicorn app.main:app --reload`)
- Start worker: `./scripts/run_worker.sh` (or `uv run celery -A app.workers.celery_app.celery_app worker -l info`)

## Reliability note (Redis eviction)
If your Redis provider enables eviction, queued Celery tasks can be dropped under memory pressure. For correctness, prefer a broker with eviction disabled (or use Postgres as the durable source of truth and re-drive “due sessions” periodically).
