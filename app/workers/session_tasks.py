"""
Session processing tasks for Celery.

Note: With the instant processing flow, session tasks are no longer used.
Processing is done inline in handle_update_v2.
This module is kept for backwards compatibility but has no active tasks.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
