from logging.config import fileConfig

from alembic import context
from alembic.operations import ops
from sqlalchemy import engine_from_config, pool
from sqlalchemy import Enum as SAEnum, text

from app.core.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401

config = context.config

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _enum_type_exists(connection, name: str, schema: str | None) -> bool:
    schema_name = schema or "public"
    row = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = :name AND n.nspname = :schema
            LIMIT 1
            """
        ),
        {"name": name, "schema": schema_name},
    ).fetchone()
    return row is not None


def _get_db_enum_values(connection, name: str, schema: str | None) -> list[str]:
    schema_name = schema or "public"
    rows = connection.execute(
        text(
            """
            SELECT e.enumlabel
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = :name AND n.nspname = :schema
            ORDER BY e.enumsortorder
            """
        ),
        {"name": name, "schema": schema_name},
    ).fetchall()
    return [row[0] for row in rows]


def _collect_enum_additions(connection, metadata: Base.metadata.__class__) -> dict[tuple[str, str | None], list[str]]:
    additions: dict[tuple[str, str | None], list[str]] = {}
    for table in metadata.tables.values():
        for column in table.columns:
            col_type = column.type
            if not isinstance(col_type, SAEnum):
                continue
            if not getattr(col_type, "native_enum", True):
                continue
            enum_name = col_type.name
            if not enum_name:
                continue
            schema = col_type.schema
            # Only emit ALTER TYPE for enums that already exist in the DB.
            # For brand-new enums, Alembic/SQLAlchemy will create the type via table creation.
            if not _enum_type_exists(connection, enum_name, schema):
                continue
            db_values = _get_db_enum_values(connection, enum_name, schema)
            metadata_values = list(col_type.enums)
            missing = [value for value in metadata_values if value not in db_values]
            if missing:
                key = (enum_name, schema)
                existing = additions.get(key, [])
                for value in missing:
                    if value not in existing:
                        existing.append(value)
                additions[key] = existing
    return additions


def _process_revision_directives(context, revision, directives) -> None:
    if not getattr(context.config.cmd_opts, "autogenerate", False):
        return
    script = directives[0]
    if not script.upgrade_ops:
        return
    additions = _collect_enum_additions(context.connection, target_metadata)
    for (enum_name, schema), values in additions.items():
        type_ref = f'"{schema}".{enum_name}' if schema else enum_name
        for value in values:
            script.upgrade_ops.ops.insert(
                0,
                ops.ExecuteSQLOp(
                    f"ALTER TYPE {type_ref} ADD VALUE IF NOT EXISTS '{value}'"
                ),
            )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=settings.database_url,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            process_revision_directives=_process_revision_directives,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
