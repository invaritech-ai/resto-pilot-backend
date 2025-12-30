# Telegram File Routing + Batching (Design Notes)

This doc captures the current agreed direction for ingesting Telegram messages/files, batching them into “sessions”, and routing them to worker pipelines for processing.

## High-level goals
- Accept user uploads via Telegram (private chats only for now).
- Batch “bursts” of user activity into a single processing session.
- Route each session (and optionally its files) to the right processing pipeline.
- Maximize accountability/observability: who uploaded what, when, and what happened next.
- Keep infra light early: store Telegram file references (Option B), fetch bytes only in workers when needed.

## Scope assumptions (current)
- Private chats only (no group chat support).
- `chat_id` equals the user’s `telegram_id` for private chats; session key is effectively per-user.
- We are not fixating on “sending files back” yet; but workers will send simple status messages (e.g., “processing now”).
- Celery is the worker system; the broker can be Redis/Upstash or AWS SQS.

## Supported upload types (expected)
- Images (photo/document)
- PDFs
- CSV
- XLS/XLSX
- TXT

Examples of user intent (non-exhaustive):
- Inventory photos (walk-in/freezer for item detection)
- Invoice/delivery photos (daily receiving)
- Vendor price portfolios/lists (monthly; often PDF/CSV/XLSX)
- Recipes, menu photos (maybe; lower priority/harder automation)

## Telegram integration model (Option B)
We store Telegram references + metadata and download bytes later (when/if needed) inside workers.

Minimum file identifiers to persist:
- `file_id` (used for re-sending and for `getFile`)
- `file_unique_id` (stable identifier for dedupe/correlation)
- `message_id`, `chat_id`, `update_id`
- filename/mime/size when present

Workers obtain bytes by:
1) `getFile(file_id)` → yields `file_path`
2) GET `https://api.telegram.org/file/bot<TOKEN>/<file_path>` to download bytes

## User “extra details” and commands
Free text is always supported. Optional, fast “session hint” commands are allowed and sticky within the current session:
- `/invoice` (invoices/receipts/delivery notes)
- `/inventory` (inventory scene photos)
- `/prices` (vendor price lists: pdf/csv/xls/xlsx)
- `/recipe`
- `/menu`
- `/done` (flush immediately; don’t wait for idle timer)
- `/reset` (clear current hint/context)
- `/help`

Router precedence:
1) Session hint command wins (if present).
2) Otherwise infer from file types + free text + lightweight heuristics.

## Batching (“session aggregator”) rules
We batch messages into a “session” with debounce behavior:
- Normal flush: 30s after last activity.
- Hard cap: 180s after session start.
- Effective flush time: `flush_at = min(last_activity_at + 30s, started_at + 180s)`.

Block/session start:
- A new session begins when a message arrives and there is no open session, or the prior session is no longer eligible to accept messages (idle gap, cap exceeded, or not open).
- No user-visible marker is required; we create a session record in the backend and attach messages to it.

## Webhook (FastAPI) responsibilities
On every update:
1) Verify Telegram webhook secret header.
2) Parse JSON.
3) Persist the update into Postgres (`telegram_sessions` + `telegram_messages`) in a single transaction.
4) Schedule a delayed Celery `flush_session(session_id, expected_last_activity_at)` at `flush_at`.
5) Return immediately with `{"status":"ok"}`.

Notes:
- In Postgres, ingestion is serialized per `chat_id` (advisory lock) to avoid race conditions under concurrency.
- The webhook should not call external APIs; it should remain “DB-only + enqueue”.

## Worker ingest responsibilities
The Celery worker (consumer) is responsible for flushing + processing:
1) `flush_session(...)` transitions eligible sessions to `processing` and enqueues:
   - `send_session_ack(session_id)` (sends “Got it — I’m on it.” once per session)
   - `process_session(session_id)`
2) `process_session(session_id)` loads the “batch evidence” and runs the router + processing pipeline.

Notes:
- Webhook response JSON is not used for delayed user feedback. Worker will send “processing now” asynchronously.
- Idempotency: Telegram + queues are at-least-once; ingest should tolerate duplicates using `update_id`/`message_id`.

## Queue + worker tasks (Celery)
Two core task types:

1) `flush_session(session_id, expected_last_activity_at)`
- If the session’s current `last_activity_at` is newer than `expected_last_activity_at`, no-op (a newer flush is scheduled).
- If eligible, atomically transition session to `processing`, then enqueue processing for that session.
- Optionally send a “Got it, processing now” message to the user (outbound Telegram `sendMessage`).

2) `process_session(session_id)`
- Load all messages/files attached to the session (the “batch evidence”).
- Run router to decide which pipeline(s) to invoke.
- Execute processing sequentially per file or as mini-batches based on router output.
- Persist processing results and status transitions.

## Reserved (“instant”) commands
Some commands should bypass batching and trigger immediate behavior (currently: `/start`, `/respond`, `/done`).
These commands are still persisted in `telegram_messages` for auditability, but they do not wait for the normal idle flush window.

## Router: inputs and outputs
Input: “BatchEvidence”
- session hint command (if any)
- all message texts/captions in the session window
- all file metadata references (type + Telegram file ids)

Output: “RoutingPlan”
- `category` (invoice/inventory/prices/recipe/menu/unknown)
- `jobs`: list of work items (per file or grouped) with the processing function name + required inputs
- optional normalization steps (e.g., “pdf->images”, “excel->table”, “image->ocr”)

## Processing pipeline sketch (by category)
Invoice/delivery docs:
- Download → normalize (orientation/format) → OCR → extract structured fields → persist

Inventory scene photos:
- Download → normalize → object/item detection → persist detections + confidence

Vendor price lists (pdf/csv/xls/xlsx/txt):
- Download → parse/normalize to table → map columns → persist vendor catalog updates

Recipes/menu:
- Download → OCR/extract → structured recipe/menu candidates → persist (likely needs review)

## Accountability & observability (non-negotiable)
Persist enough to answer:
- Who uploaded it? (`user_id`, `telegram_id`)
- What was uploaded? (file ids + type + metadata)
- When? (`received_at`, `session_id`, `message_id`, `update_id`)
- What happened? (status transitions, worker attempts, errors, outputs)

Recommended correlation id:
- Use `session_id` as the top-level correlation id across logs, queue messages, and DB.

## Open decisions (not answered yet)
- Session state source of truth for v1: DB-only vs Redis state (DB still as the audit log).
- Exact DB schema names/models and where they live in `app/db/models`.
- Task concurrency + rate limiting per user/session.
