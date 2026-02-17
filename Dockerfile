FROM python:3.12-slim

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Pre-compile .pyc so first-import is instant
    UV_COMPILE_BYTECODE=1 \
    # Required in Docker: can't hardlink across filesystems
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    antiword \
    ca-certificates \
    poppler-utils \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Layer 1: dependencies (cached until pyproject.toml / uv.lock change) ---
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Layer 2: project code (cached until app/ or alembic/ change) ---
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
