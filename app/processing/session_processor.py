"""
Session processor - DEPRECATED.

With the instant processing flow, session processing is no longer used.
Processing is done inline in app.telegram.handler.handle_update_v2().
This module is kept for backward compatibility but has no active code.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
