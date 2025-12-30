from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class OpenAIError(RuntimeError):
    pass


def chat_completions_create(
    *,
    settings: Settings,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise OpenAIError("OpenAI API key is not configured (APP_OPENAI_API_KEY)")

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title
    if extra_headers:
        headers.update(extra_headers)

    payload: dict[str, Any] = {
        "model": settings.openai_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    timeout = httpx.Timeout(settings.openai_timeout_seconds)
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("openai_http_error", extra={"error": repr(exc)})
        raise OpenAIError(f"OpenAI request failed: {exc}") from exc

    return resp.json()


def create_chat_completion_text(
    *,
    settings: Settings,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    extra_headers: dict[str, str] | None = None,
) -> str:
    data = chat_completions_create(
        settings=settings,
        messages=messages,
        temperature=temperature,
        extra_headers=extra_headers,
    )
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise OpenAIError(f"Unexpected OpenAI response shape: {data}") from exc

    if not isinstance(content, str) or not content.strip():
        raise OpenAIError("OpenAI returned empty content")
    return content.strip()
