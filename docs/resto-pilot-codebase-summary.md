# Resto Pilot codebase summary (current)

Resto Pilot is an AI-powered assistant for restaurant workflows. Today it’s centered around a Telegram bot + a batching pipeline that turns multiple messages/files into a single “session” for processing.

## Architecture & infrastructure
- **Web framework**: FastAPI (`app/main.py`) with an app factory `create_app(...)` for tests and serverless usage.
- **Serverless entrypoint**: Mangum wrapper for Lambda (`app/handler.py`).
- **Database**: Postgres (commonly Neon) via SQLAlchemy + Alembic migrations (`alembic/`).
- **Background workers**: Celery tasks (`app/workers/tasks.py`) plus a worker DB session helper (`app/workers/db.py`).
- **Broker**: Redis/Upstash or AWS SQS (configured via `APP_CELERY_BROKER_URL`). Notes on SQS: `docs/aws-sqs-celery-broker-notes.md`.
- **Auth/security**:
  - JWT access tokens for API endpoints (`app/api/security.py`).
  - Telegram WebApp init data verification for webapp auth (`app/api/v1/routes/auth.py`).
  - Telegram webhook secret header validation (`X-Telegram-Bot-Api-Secret-Token`) (`app/api/v1/routes/telegram.py`).
- **Observability**:
  - Structured logging (`app/core/logging.py`).
  - DB-backed lifecycle/audit events in `processing_events` (`app/db/models/processing_events.py`).

## Telegram integration & smart batching
### Webhook (FastAPI)
`POST /api/v1/telegram` (`app/api/v1/routes/telegram.py`)
- Validates secret header.
- Returns `503` if webhook secret or broker URL is missing.
- When batching is enabled, persists the update into Postgres (`telegram_sessions` + `telegram_messages`), schedules a delayed `flush_session(...)`, and returns `{"status":"ok"}`.
- When batching is disabled, enqueues `handle_telegram_update.delay(update)` and returns `{"status":"ok"}`.

### Worker entrypoint
`handle_telegram_update(update)` (`app/workers/tasks.py`)
- Opens a DB session (`worker_db_session()`).
- Loads settings (`get_settings()`).
- Delegates all routing/business logic to `handle_update(update, db, settings)` (`app/telegram/handler.py`).

### Sessions + messages (batch evidence)
`ingest_update(...)` (`app/telegram/ingest.py`) persists “what arrived” and “how we grouped it”:
- **telegram_sessions** (`app/db/models/telegram_session.py`): one row per session window, keyed by `chat_id`.
  - Maintains `started_at`, `last_activity_at`, `flush_at`, `status` (`open|processing|closed`), optional `hint_command`, `closed_at`.
  - Batching rules are controlled by settings:
    - `telegram_batch_idle_seconds` (default 30s)
    - `telegram_batch_max_seconds` (default 180s)
- **telegram_messages** (`app/db/models/telegram_messages.py`): one row per ingested Telegram update/message (text/caption + file metadata).
  - `update_id` is unique for idempotency.

### Hint commands (including caption support)
The “hint command” is stored on the session (`telegram_sessions.hint_command`) and influences routing/processing.
- Command extraction is shared and supports **caption commands**: `extract_command(text, caption)` in `app/telegram/commands.py`.
- Ingest uses command extraction to set hints from either text or caption (`app/telegram/ingest.py`).

### Reserved commands + force flush
Some commands bypass batching:
- `/start`:
  - Immediate behavior is handled by `process_update(...)` (`app/telegram/processor.py`) which can create/register the user and accept deep-link invite codes.
  - `/start` is also persisted into the audit tables for history.
- `/done` and `/respond`:
  - Force-flush flow: the webhook seals the latest open session (`open -> processing`) and enqueues `send_session_ack` + `process_session`.
  - If there is **no open session**, the update is still accepted but no work is enqueued.
- `/confirm` and `/cancel`:
  - Instant resolution of pending DB actions (deterministic apply/cancel) without waiting for batching.

## Background tasks (Celery)
Defined in `app/workers/tasks.py`:
- `flush_session(session_id, expected_last_activity_at)`:
  - Used for the normal debounce window.
  - Uses DB locking + the `expected_last_activity_at` guard to no-op stale flushes.
  - When it seals the session, it enqueues `send_session_ack` and `process_session`.
- `send_session_ack(session_id)`:
  - Sends a best-effort short backchannel ack once per session (guarded by `telegram_sessions.ack_sent_at`); may be skipped for naturalness.
- `process_session(session_id)`:
  - Loads all messages for the session from the DB.
  - Runs a cheap on-topic gate: if off-topic, sends one redirect then ghosts until on-topic again.
  - Runs a general-purpose agent loop (`app/ai/agent.py`) with tool-calling support:
    - Agent can autonomously decide which tools to use (database operations, restaurant lookups, etc.)
    - Each LLM call in the agent loop is recorded individually in `llm_calls` for accurate cost tracking
    - Tools are defined in `app/ai/db_tools.py` and enforce role/scope-based access controls
  - Generates assistant reply and persists telemetry (`llm_calls`) and outbound messages (`telegram_outgoing_messages`).

## Agent architecture
The bot uses a general-purpose agent loop (`app/ai/agent.py`) that supports autonomous tool-calling:
- **Tool-calling loop**: The agent can make multiple LLM calls in a conversation, using tools to interact with the database and services.
- **Database tools** (`app/ai/db_tools.py`): Tools for listing available tables, reading restaurants, creating/updating restaurants, etc.
- **Access control**: All database operations enforce role-based and scope-based access controls via `app/policies/db_policy.py`.
- **Telemetry**: Each LLM call in the agent loop is recorded individually in `llm_calls` with `openrouter_generation_id` for cost attribution and backfill.

## Processing timeline (processing_events)
For a typical session you'll see events like:
- `ingested_update` (one per message/update ingested)
- `session_flushed` (auto flush) or `session_sealed_by_command` (force flush)
- `router_plan_v0`
- `assistant_reply_generated_v0` (includes tool calls made)
- `assistant_reply_sent_v0`
- `session_processed_v0`

## REST API endpoints
Routes live in `app/api/v1/routes/` and are included under `/api/v1` via `app/api/router.py`.
- Auth (`app/api/v1/routes/auth.py`)
  - `POST /auth/telegram-webapp`
  - `GET /me`
- Restaurants (`app/api/v1/routes/restaurants.py`)
  - `POST /restaurants`
  - `GET /restaurants`
  - `GET /restaurants/{restaurant_id}/members`
  - `POST /restaurants/{restaurant_id}/invites` (returns a deep link)
- Users (`app/api/v1/routes/users.py`)
  - `GET /users`
  - `POST /users`

## Telegram command menu (optional)
To manage the Telegram command menu programmatically:
- `docs/telegram-bot-commands.md`
