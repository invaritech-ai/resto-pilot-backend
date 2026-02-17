"""
Telegram update Celery task - minimal stub.

Full bot processing logic will be rebuilt in Phase 5.
"""
from __future__ import annotations

import logging

from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict) -> None:
    """Process a Telegram update in the background."""
    task_id = _get_task_id()
    message = (update.get("message") or update.get("edited_message") or {}) if isinstance(update, dict) else {}
    chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None
    update_id = update.get("update_id") if isinstance(update, dict) else None

    logger.info(
        "celery_task_started name=handle_telegram_update task_id=%s update_id=%s chat_id=%s",
        task_id, update_id, chat_id,
    )

    # TODO: Implement full bot processing in Phase 5
    logger.info("handle_telegram_update_stub: processing not yet implemented")
