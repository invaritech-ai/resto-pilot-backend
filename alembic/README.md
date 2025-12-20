# Alembic (DB Migrations)

This project uses Alembic to manage database schema migrations. Alembic is configured via `alembic.ini` and `alembic/env.py`.

## Prerequisites

- Run commands from the repo root (so `alembic.ini` is found), or pass `-c alembic.ini`.
- Set `APP_DATABASE_URL` (loaded automatically from `.env` via `app/core/config.py`), for example:
  - SQLite (default): `sqlite:///./data/app.db`
  - Postgres: `postgresql+psycopg://user:pass@localhost:5432/dbname`

If you use `uv`, prefer `uv run alembic ...`. If you have an activated venv, you can run `alembic ...` directly.

## Common commands

### Status / introspection

- Current revision: `alembic current`
- List revision history: `alembic history`
- Verbose history: `alembic history --verbose`
- Show head revision(s): `alembic heads`

### Create migrations

- Create an empty revision file:
  - `alembic revision -m "add users table"`
- Autogenerate from SQLAlchemy models (`Base.metadata`):
  - `alembic revision --autogenerate -m "add users table"`

Migrations are created under `alembic/versions/`.

### Upgrade (apply migrations)

- Upgrade to the latest: `alembic upgrade head`
- Upgrade to a specific revision: `alembic upgrade <revision_id>`
- Upgrade by one revision: `alembic upgrade +1`

### Downgrade (rollback migrations)

- Downgrade by one revision: `alembic downgrade -1`
- Downgrade to a specific revision: `alembic downgrade <revision_id>`
- Downgrade all the way down: `alembic downgrade base`

### Stamp (mark as applied without running SQL)

- Mark DB as up-to-date: `alembic stamp head`
- Mark DB as a specific revision: `alembic stamp <revision_id>`

### Generate SQL without executing

- SQL for an upgrade: `alembic upgrade head --sql`
- SQL for a downgrade: `alembic downgrade -1 --sql`

## Examples with `uv`

- `uv run alembic upgrade head`
- `uv run alembic downgrade -1`
- `uv run alembic revision --autogenerate -m "add invite codes"`
