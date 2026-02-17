"""
Celery task registry.
"""
from app.workers.telegram_tasks import handle_telegram_update  # noqa: F401
