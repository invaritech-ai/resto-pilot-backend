"""
Webhook notification system for file processing progress updates.

Sends HTTP POST notifications to client-provided webhook URLs with
retry logic and exponential backoff.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, UTC
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


def send_webhook_notification(
    webhook_url: str,
    run_id: UUID,
    status: str,
    progress_percentage: float,
    pages_processed: int,
    pages_total: int | None,
    staging_id: UUID | None = None,
    error_message: str | None = None,
    max_retries: int = 3,
) -> bool:
    """
    Send webhook notification with retry logic.

    Args:
        webhook_url: Client webhook endpoint URL
        run_id: FileProcessingRuns ID
        status: Current processing status ("processing", "completed", "failed")
        progress_percentage: Progress as percentage (0-100)
        pages_processed: Number of pages completed
        pages_total: Total pages in document
        staging_id: FileProcessingStaging ID (if completed)
        error_message: Error message (if failed)
        max_retries: Maximum retry attempts

    Returns:
        True if notification sent successfully, False otherwise
    """
    payload = {
        "run_id": str(run_id),
        "status": status,
        "progress_percentage": progress_percentage,
        "pages_processed": pages_processed,
        "pages_total": pages_total,
        "timestamp": datetime.now(UTC).isoformat() + "Z",
    }

    if staging_id:
        payload["staging_id"] = str(staging_id)
        payload["data_url"] = f"/api/v1/files/{staging_id}"

    if error_message:
        payload["error_message"] = error_message

    for attempt in range(max_retries):
        try:
            response = httpx.post(
                webhook_url,
                json=payload,
                timeout=10.0,
                headers={"User-Agent": "RestoPilot/1.0", "Content-Type": "application/json"},
            )
            response.raise_for_status()

            logger.info(
                "webhook_notification_sent",
                extra={
                    "webhook_url": webhook_url,
                    "run_id": str(run_id),
                    "status": status,
                    "attempt": attempt + 1,
                    "status_code": response.status_code,
                },
            )
            return True

        except httpx.HTTPError as exc:
            logger.warning(
                "webhook_notification_failed",
                extra={
                    "webhook_url": webhook_url,
                    "run_id": str(run_id),
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "error": str(exc),
                },
            )

            if attempt == max_retries - 1:
                # Final attempt failed
                logger.error(
                    "webhook_notification_exhausted",
                    extra={
                        "webhook_url": webhook_url,
                        "run_id": str(run_id),
                        "error": str(exc),
                    },
                )
                return False

            # Wait before retry (exponential backoff: 1s, 2s, 4s)
            sleep_duration = 2**attempt
            time.sleep(sleep_duration)

    return False
