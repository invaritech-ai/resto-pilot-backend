from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.ai.openai_client import OpenAIError
from app.core.config import Settings


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


def fetch_openrouter_generation(
    *, settings: Settings, generation_id: str, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    """
    Fetch OpenRouter generation details, including billed cost, via GET /generation?id=...

    Requires APP_OPENAI_BASE_URL to be OpenRouter (e.g. https://openrouter.ai/api/v1).
    """
    gen_id = generation_id.strip()
    if not gen_id:
        raise ValueError("generation_id is required")

    if not settings.openai_api_key:
        raise OpenAIError("OpenAI API key is not configured (APP_OPENAI_API_KEY)")

    base_url = settings.openai_base_url.rstrip("/")
    url = f"{base_url}/generation"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    resp = httpx.get(url, headers=headers, params={"id": gen_id}, timeout=timeout_seconds)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise OpenAIError(f"Unexpected OpenRouter generation response shape: {data!r}")
    return data
