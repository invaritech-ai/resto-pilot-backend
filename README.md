## Overview

This is a Telegram bot with intent classification and static, deterministic actions.
Messages are processed instantly (no batching): the webhook enqueues updates to Celery,
the worker classifies intent, executes a fixed operation, and returns a template response.
Anything outside supported intents returns "I can't help with that."

Supported intents and manual test paths: `docs/intents-and-paths.md`.

## Local dev

- Copy `.env.example` to `.env` and set `APP_DATABASE_URL` to your Neon connection string (e.g. `postgresql+psycopg://user:pass@host/db?sslmode=require`).
- Install deps and run the API: `./scripts/run_api.sh` (or `uv run uvicorn app.main:app --reload`).
- Create your first migration: `alembic revision --autogenerate -m "init"` then `alembic upgrade head`.
- Configure CORS origins/settings via `.env` as needed.

## Background workers (Celery)

The Telegram webhook enqueues updates to Celery for instant processing. A running Celery
worker is required for message handling and file processing tasks.

- Broker options:
    - Redis/Upstash: `APP_CELERY_BROKER_URL=rediss://...`
    - AWS SQS: `APP_CELERY_BROKER_URL=sqs://` plus `APP_CELERY_SQS_QUEUE_URL` and `APP_CELERY_SQS_REGION`
- Start Redis (local, if using Redis broker): `redis-server`
- Start worker: `./scripts/run_worker.sh` (or `uv run celery -A app.workers.celery_app.celery_app worker -l info`)
- Start both API + worker: `./scripts/run_dev.sh`
- Notes on SQS + reliability: `docs/aws-sqs-celery-broker-notes.md`

## LLM configuration (OpenRouter/OpenAI-compatible)

The deterministic architecture uses specialized LLM blocks, each configurable via environment variables:

### Core Deterministic Blocks
- `APP_OPENAI_MODEL`: Fallback model for all blocks (default: `gpt-4o-mini`)
- `APP_ACK_MODEL`: Fast acknowledgments ("Got it...") - use cheapest/fastest model
- `APP_PLANNER_MODEL`: Intent classification & tool selection - use most capable model
- `APP_CLARIFICATION_MODEL`: Rephrase clarification questions (optional) - conversational model
- `APP_PRESENTER_MODEL`: Format final responses - good at summarization

### File Processing
- `APP_VISION_MODEL`: Invoice/price list OCR extraction
- `APP_OPENAI_FILE_TYPE_MODEL`: File type detection (price list vs invoice)
- `APP_OPENAI_AUDIO_MODEL`: Audio transcription (default: `whisper-1`)
- `APP_OPENAI_VIDEO_MODEL`: Video processing

**Telemetry & Cost Tracking**: Every LLM call is recorded in `llm_calls` with `openrouter_generation_id` for cost attribution. Cost backfill is scheduled automatically via Celery tasks to fetch actual costs from OpenRouter.

### Example Configuration
```bash
APP_OPENAI_MODEL=openai/gpt-4o-mini          # Fallback
APP_ACK_MODEL=openai/gpt-4o-nano             # Cheapest for acks
APP_PLANNER_MODEL=anthropic/claude-3-sonnet  # Smart for planning
APP_PRESENTER_MODEL=openai/gpt-4o            # Good at formatting
APP_VISION_MODEL=openai/gpt-4o-vision        # For file OCR
```

## Vision model configuration (file processing)

Invoice/price list/inventory photo processing uses a vision model:

- `APP_VISION_MODEL`, `APP_VISION_API_KEY`, `APP_VISION_BASE_URL`
- If unset, the vision client falls back to `APP_OPENAI_*` values.

## Package for AWS Lambda (FastAPI + Mangum)

- Handler entrypoint is `app.handler.handler` (see `app/handler.py`).
- Build the deployable zip from repo root for Lambda Python 3.12:
    ```
    rm -rf build && mkdir -p build/lambda
    uv pip install --target build/lambda --python-platform x86_64-manylinux2014 --python-version 3.12 --only-binary=:all: .
    cp -r app build/lambda/
    cd build/lambda && zip -r ../resto-pilot-lambda.zip .
    ```
    Uses Python 3.12 runtime.

## Deploy (manual, console)

1. Lambda: Console → Lambda → Create function (Python 3.12) → Upload `resto-pilot-lambda.zip` → Handler `app.handler.handler`.
2. API Gateway: Create HTTP API → route `POST /api/v1/telegram` → integration = your Lambda → Deploy. Accept the permission prompt so API Gateway can invoke Lambda.

## Telegram webhook

- Set the webhook (replace token and URL):
    ```
    https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://<api-id>.execute-api.<region>.amazonaws.com/api/v1/telegram&secret_token=<YOUR_WEBHOOK_SECRET>
    ```
- Configure `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN=<YOUR_WEBHOOK_SECRET>` in your server/Lambda environment.
- Test by sending a message; logs appear in CloudWatch `/aws/lambda/<function-name>`.
- Manage Telegram command menu (optional): `docs/telegram-bot-commands.md`

## Deploy to Coolify (temporary)

This repo includes a `Dockerfile` and `docker-compose.yaml` to run the API + Celery worker on a server.

- In Coolify: create a new resource from this git repo and choose **Docker Compose** (it will look for `docker-compose.yaml`).
- Expose the `api` service on port `8000` and attach a domain (Coolify will handle TLS).
- If deployment fails with “port is already allocated”, set `API_HOST_PORT` in Coolify to a free port (the container still listens on `8000`).
- Set environment variables in Coolify (both services need them):
    - `APP_DATABASE_URL` (Neon Postgres)
    - `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN`
    - `APP_TELEGRAM_BOT_TOKEN`
    - `APP_CELERY_BROKER_URL=sqs://` + `APP_CELERY_SQS_QUEUE_URL` + `APP_CELERY_SQS_REGION`
    - AWS creds (either `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` + `AWS_DEFAULT_REGION`, or an IAM role if your server supports it)
    - OpenRouter/OpenAI-compatible settings:
        - `APP_OPENAI_BASE_URL=https://openrouter.ai/api/v1`
        - `APP_OPENAI_API_KEY=<OPENROUTER_KEY>`
        - `APP_OPENAI_MODEL=<main-model-slug>`
        - Optional: `APP_OPENAI_GATE_MODEL=<cheap-gate-model-slug>`
        - Optional: `APP_OPENROUTER_HTTP_REFERER` + `APP_OPENROUTER_TITLE`
    - Run DB migrations once (Coolify exec into the `api` container): `alembic upgrade head`
    - Point Telegram webhook to your public domain: `https://<your-domain>/api/v1/telegram` with the same `secret_token`.

Notes:

- Scripts may need permissions once: `chmod +x scripts/*.sh`.
- If you deploy the API to Lambda/serverless but keep Celery, run the Celery worker separately (e.g. ECS/Fargate, EC2, Fly, or a small VM).

---

```
┌─────────────────────────────────────────────────────┐
│                   Message Input                      │
│            (Telegram OR Test API)                    │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │    Ack (optional)      │ ← Cheap LLM
         │  "Got it, one moment"  │
         └───────────┬────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │   Load Context         │ ← Last 20 messages
         │   (conversation +      │   + user state
         │    user state)         │
         └───────────┬────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │      PLANNER           │ ← Decision LLM
         │  Decides: clarify OR   │   (can use lookup tools
         │  call_tool (ONE only)  │    max 3 reads)
         └───────────┬────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │    VALIDATION          │ ← Deterministic
         │  - No UUIDs?           │
         │  - Schema valid?       │
         │  - Tool exists?        │
         └───────────┬────────────┘
                     │
           ┌─────────┴─────────┐
           │                   │
           ▼                   ▼
    ┌──────────┐      ┌──────────────┐
    │ Clarify  │      │   Execute     │ ← Deterministic
    │ Question │      │   ONE Tool    │   (resolve entities,
    └──────────┘      └───────┬───────┘    run CRUD)
                              │
                              ▼
                     ┌────────────────┐
                     │   PRESENTER    │ ← Cheap LLM
                     │  Format result │   (or deterministic
                     │  as user text  │    templates)
                     └────────┬───────┘
                              │
                              ▼
                     ┌────────────────┐
                     │  Response      │
                     │  (Telegram or  │
                     │   console)     │
                     └────────────────┘
```
