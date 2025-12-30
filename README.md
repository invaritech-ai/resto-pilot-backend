## Local dev
- Copy `.env.example` to `.env` and set `APP_DATABASE_URL` to your Neon connection string (e.g. `postgresql+psycopg://user:pass@host/db?sslmode=require`).
- Install deps and run the API: `./scripts/run_api.sh` (or `uv run uvicorn app.main:app --reload`).
- Create your first migration: `alembic revision --autogenerate -m "init"` then `alembic upgrade head`.
- Configure CORS origins/settings via `.env` as needed.

## Background workers (Celery)
When batching is enabled (`APP_TELEGRAM_BATCHING_ENABLED=true`), the Telegram webhook persists incoming updates to Postgres and schedules Celery tasks for flushing + processing sessions. A running Celery worker is required for:
- scheduled session flushes (`flush_session`)
- sending session ack messages (`send_session_ack`)
- processing sessions (`process_session`)
- non-batching flows and `/start` routing (`handle_telegram_update`)

- Broker options:
  - Redis/Upstash: `APP_CELERY_BROKER_URL=rediss://...`
  - AWS SQS: `APP_CELERY_BROKER_URL=sqs://` plus `APP_CELERY_SQS_QUEUE_URL` and `APP_CELERY_SQS_REGION`
- Start Redis (local, if using Redis broker): `redis-server`
- Start worker: `./scripts/run_worker.sh` (or `uv run celery -A app.workers.celery_app.celery_app worker -l info`)
- Start both API + worker: `./scripts/run_dev.sh`
- Notes on SQS + reliability: `docs/aws-sqs-celery-broker-notes.md`

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
1) Lambda: Console → Lambda → Create function (Python 3.12) → Upload `resto-pilot-lambda.zip` → Handler `app.handler.handler`.
2) API Gateway: Create HTTP API → route `POST /api/v1/telegram` → integration = your Lambda → Deploy. Accept the permission prompt so API Gateway can invoke Lambda.

## Telegram webhook
- Set the webhook (replace token and URL):
  ```
  https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://<api-id>.execute-api.<region>.amazonaws.com/api/v1/telegram&secret_token=<YOUR_WEBHOOK_SECRET>
  ```
- Configure `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN=<YOUR_WEBHOOK_SECRET>` in your server/Lambda environment.
- Test by sending a message; logs appear in CloudWatch `/aws/lambda/<function-name>`.
- Manage Telegram command menu (optional): `docs/telegram-bot-commands.md`

## Deploy to Coolify (temporary)
This repo includes a `Dockerfile` and `docker-compose.yml` to run the API + Celery worker on a server.

- In Coolify: create a new resource from this git repo and choose **Docker Compose**.
- Expose the `api` service on port `8000` and attach a domain (Coolify will handle TLS).
- Set environment variables in Coolify (both services need them):
  - `APP_DATABASE_URL` (Neon Postgres)
  - `APP_TELEGRAM_WEBHOOK_SECRET_TOKEN`
  - `APP_TELEGRAM_BOT_TOKEN`
  - `APP_TELEGRAM_BATCHING_ENABLED=true`
  - `APP_CELERY_BROKER_URL=sqs://` + `APP_CELERY_SQS_QUEUE_URL` + `APP_CELERY_SQS_REGION`
  - AWS creds (either `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` + `AWS_DEFAULT_REGION`, or an IAM role if your server supports it)
  - OpenRouter/OpenAI-compatible settings:
    - `APP_OPENAI_BASE_URL=https://openrouter.ai/api/v1`
    - `APP_OPENAI_API_KEY=<OPENROUTER_KEY>`
    - `APP_OPENAI_MODEL=<model-slug>`
    - Optional: `APP_OPENROUTER_HTTP_REFERER` + `APP_OPENROUTER_TITLE`
- Run DB migrations once (Coolify exec into the `api` container): `alembic upgrade head`
- Point Telegram webhook to your public domain: `https://<your-domain>/api/v1/telegram` with the same `secret_token`.

Notes:
- Scripts may need permissions once: `chmod +x scripts/*.sh`.
- If you deploy the API to Lambda/serverless but keep Celery, run the Celery worker separately (e.g. ECS/Fargate, EC2, Fly, or a small VM). In batching mode, the API persists updates and Celery performs flushing/ack/processing.
