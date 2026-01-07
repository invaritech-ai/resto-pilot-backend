"""
Backward compatibility wrapper for db_tools.

This module has been refactored into app/ai/db_tools/ package.
All functionality is now in the modular structure.
"""

from __future__ import annotations

# Re-export for backward compatibility
from app.ai.db_tools import create_db_tools

__all__ = ["create_db_tools"]
