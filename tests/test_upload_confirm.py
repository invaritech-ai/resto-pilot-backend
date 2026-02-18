"""Unit tests for app/telegram/handlers/buttons.py — Step 8 upload confirm."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.telegram.handlers.buttons import handle

STAGING_ID = uuid.uuid4()
STAGING_HEX = STAGING_ID.hex
SUPPLIER_ID = uuid.uuid4()
SUPPLIER_HEX = SUPPLIER_ID.hex
RESTAURANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()
