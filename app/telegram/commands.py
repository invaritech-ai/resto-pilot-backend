from __future__ import annotations


def extract_command(
    text: str | None, caption: str | None
) -> tuple[str | None, str | None]:
    """
    Extract a Telegram-style command from message text/caption.

    Returns (command, args) where:
    - command is the normalized command (e.g. "/start") or None
    - args is the remainder after the command (e.g. "CODE") or None

    Rules:
    - Prefer non-empty `text`; otherwise use `caption`.
    - The command must be the first token and start with "/".
    - Strip optional "@botname" suffix (e.g. "/start@mybot").
    - Lowercase the command token.
    """
    content = None
    if isinstance(text, str) and text.strip():
        content = text
    elif isinstance(caption, str) and caption.strip():
        content = caption

    if content is None:
        return None, None

    stripped = content.strip()
    if not stripped:
        return None, None

    parts = stripped.split(maxsplit=1)
    first_token = parts[0]
    rest = parts[1].strip() if len(parts) == 2 else None

    if not first_token.startswith("/"):
        return None, None

    command = first_token.split("@", 1)[0].lower()
    return command, (rest or None)

