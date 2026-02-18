"""Shared constants for the Telegram bot.

This module centralizes business constants to avoid "keep in sync" drift
between multiple files.
"""

# Reset words that trigger the global reset handler (Priority 1).
# Case-insensitive matching after strip().
# /start is included here so it cannot fall through to the command handler.
RESET_WORDS: frozenset[str] = frozenset(
    {
        "home",
        "menu",
        "cancel",
        "exit",
        "/start",
    }
)

# Lowercase version for case-insensitive comparison
RESET_WORDS_LOWER: frozenset[str] = frozenset(w.lower() for w in RESET_WORDS)
