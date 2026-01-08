"""
Database tools for the intent-driven bot.

This package provides intent-based database tools organized by domain.
Each module contains tools for a specific domain (profile, restaurants, staff, invites, etc.).

To add new tools:
1. Create a new module file (e.g., inventory.py, suppliers.py)
2. Create a factory function (e.g., create_inventory_tools)
3. Import and call it in base.py's create_db_tools function
"""

from .base import create_db_tools

__all__ = ["create_db_tools"]
