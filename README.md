## Local dev
- Copy `.env.example` to `.env` and set `APP_DATABASE_URL` to your Neon connection string (e.g. `postgresql+psycopg://user:pass@host/db?sslmode=require`).
- Install deps and run the API: `uv run uvicorn app.main:app --reload`.
- Create your first migration: `alembic revision --autogenerate -m "init"` then `alembic upgrade head`.
- Configure CORS origins/settings via `.env` as needed.

## Background workers (Celery)
The Telegram webhook endpoint enqueues updates to Celery and returns `{"status":"ok"}` immediately. A running Celery worker is required for Telegram updates to be persisted/processed.

- Start Redis (local): `redis-server`
- Start worker: `uv run celery -A app.workers.celery_app.celery_app worker -l info`

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

Notes:
- If you deploy the API to Lambda/serverless but keep Celery, run the Celery worker separately (e.g. ECS/Fargate, EC2, Fly, or a small VM). The API enqueues work; the worker does the DB writes and outbound Telegram Bot API calls.
