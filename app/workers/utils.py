"""
Utility functions for Celery workers.
"""

from __future__ import annotations

import datetime as dt
import uuid

from celery import current_task


def _coerce_utc(value: dt.datetime) -> dt.datetime:
    """
    Normalize datetimes to UTC-aware.

    SQLite may return naive datetimes even when columns are declared with
    timezone=True; treat naive values as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _parse_uuid(value: str) -> uuid.UUID:
    """Parse a string UUID."""
    return uuid.UUID(value)


def _get_task_id() -> str | None:
    """Get the current Celery task ID."""
    request = getattr(current_task, "request", None)
    return getattr(request, "id", None)
