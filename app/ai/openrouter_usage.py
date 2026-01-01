from __future__ import annotations

from typing import Any, Mapping


def extract_openrouter_usage(data: Mapping[str, Any]) -> dict[str, int] | None:
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        return None

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    if not isinstance(prompt_tokens, int) or prompt_tokens < 0:
        return None
    if not isinstance(completion_tokens, int) or completion_tokens < 0:
        return None
    if not isinstance(total_tokens, int) or total_tokens < 0:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

