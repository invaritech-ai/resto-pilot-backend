"""Telegram update Celery task."""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.services.context_service import ContextService
from app.services.user_service import UserService
from app.telegram.handlers.onboarding import handle as handle_onboarding, needs_onboarding
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id

logger = logging.getLogger(__name__)


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict) -> None:
    """Process a Telegram update: get-or-create user, onboard or route."""
    task_id = _get_task_id()
    settings = get_settings()

    message = (update.get("message") or update.get("edited_message") or {}) if isinstance(update, dict) else {}
    chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None
    update_id = update.get("update_id") if isinstance(update, dict) else None

    from_data = (message.get("from") or {}) if isinstance(message, dict) else {}
    telegram_id: int | None = from_data.get("id") or chat_id
    username: str | None = from_data.get("username")

    logger.info(
        "celery_task_started name=handle_telegram_update task_id=%s update_id=%s chat_id=%s",
        task_id, update_id, chat_id,
    )

    if not telegram_id or not chat_id:
        logger.warning("handle_telegram_update: missing telegram_id or chat_id, skipping")
        return

    with worker_db_session() as db:
        user_svc = UserService(db)
        ctx_svc = ContextService(db)

        user, created = user_svc.get_or_create(
            telegram_id=telegram_id,
            chat_id=chat_id,
            username=username,
        )

        if created:
            logger.info("new_user_created telegram_id=%s", telegram_id)

        user_svc.update_last_interaction(user)

        if needs_onboarding(user):
            handle_onboarding(update, user, db, ctx_svc, settings)
        else:
            # TODO: wire Router in next step
            logger.info("handle_telegram_update: user onboarded, routing not yet wired")

        db.commit()
