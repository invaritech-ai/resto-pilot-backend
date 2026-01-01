from __future__ import annotations

from typing import Any, Mapping


def extract_openrouter_generation_id(
    *, headers: Mapping[str, str] | None = None, data: Mapping[str, Any] | None = None
) -> str | None:
    """
    Best-effort extraction of OpenRouter "generation id" (usually starts with "gen-").

    OpenRouter may expose this via response headers and/or response body depending on
    provider and API compatibility mode.
    """
    if headers:
        lowered = {str(k).lower(): v for k, v in headers.items()}
        for key in (
            "x-openrouter-generation-id",
            "openrouter-generation-id",
            "x-generation-id",
            "x-openrouter-id",
        ):
            value = lowered.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if data:
        value = data.get("id")
        if isinstance(value, str) and value.strip().startswith("gen-"):
            return value.strip()

        generation = data.get("generation")
        if isinstance(generation, Mapping):
            gen_id = generation.get("id")
            if isinstance(gen_id, str) and gen_id.strip():
                return gen_id.strip()

    return None

