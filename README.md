## Local dev
- Install deps and run `uvicorn app.main:app --reload`.
- Create your first migration: `alembic revision --autogenerate -m "init"` then `alembic upgrade head`.
- Configure CORS origins/settings via `.env` as needed.

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
  https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://<api-id>.execute-api.<region>.amazonaws.com/api/v1/telegram
  ```
- Test by sending a message; logs appear in CloudWatch `/aws/lambda/<function-name>`.
