from __future__ import annotations

import logging
import os
import ssl

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

if settings.aws_profile and not os.environ.get("AWS_PROFILE"):
    os.environ["AWS_PROFILE"] = settings.aws_profile
    logger.info("aws_profile_configured source=settings value=%s", settings.aws_profile)

celery_app = Celery(
    "resto_pilot",
    broker=settings.celery_broker_url or None,
    backend=settings.celery_result_backend or None,
    include=[
        "app.workers.telegram_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    # Fairness + reliability defaults (esp. helpful with SQS + >1 concurrency).
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

logger.info(
    "celery_app_initialized broker_url=%s result_backend_configured=%s",
    settings.celery_broker_url,
    bool(settings.celery_result_backend),
)

if (settings.celery_broker_url or "").startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
    logger.info("celery_broker_configured kind=rediss")

if (settings.celery_broker_url or "").startswith("sqs://"):
    if not settings.celery_sqs_queue_url:
        raise RuntimeError(
            "APP_CELERY_SQS_QUEUE_URL is required when APP_CELERY_BROKER_URL starts with sqs://"
        )

    logger.info(
        "celery_broker_configured kind=sqs queue_name=%s region=%s queue_url=%s",
        settings.celery_sqs_queue_name,
        settings.celery_sqs_region,
        settings.celery_sqs_queue_url,
    )

    celery_app.conf.worker_enable_remote_control = False
    celery_app.conf.task_create_missing_queues = False
    celery_app.conf.task_default_queue = settings.celery_sqs_queue_name
    celery_app.conf.broker_transport_options = {
        "region": settings.celery_sqs_region or None,
        "visibility_timeout": settings.celery_sqs_visibility_timeout_seconds,
        "wait_time_seconds": settings.celery_sqs_wait_time_seconds,
        "predefined_queues": {
            settings.celery_sqs_queue_name: {
                "url": settings.celery_sqs_queue_url,
                "region": settings.celery_sqs_region or None,
            }
        },
    }
