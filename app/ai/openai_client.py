from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class OpenAIError(RuntimeError):
    pass


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc: httpx.HTTPError) -> bool:
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    )


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

    # Use a longer read timeout for "thinking" responses without inflating connect/pool timeouts.
    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.openai_timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    attempts = max(0, int(settings.openai_max_retries)) + 1
    retry_initial = float(settings.openai_retry_initial_seconds)
    retry_max = float(settings.openai_retry_max_seconds)

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code >= 400 and _is_retryable_status(resp.status_code) and attempt < attempts:
                logger.warning(
                    "openai_retryable_status",
                    extra={
                        "status_code": resp.status_code,
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < attempts and _is_retryable_exception(exc):
                logger.warning(
                    "openai_retryable_error",
                    extra={
                        "error": repr(exc),
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            logger.exception("openai_http_error", extra={"error": repr(exc)})
            raise OpenAIError(f"OpenAI request failed: {exc}") from exc

    raise OpenAIError(f"OpenAI request failed: {last_exc!r}")


def chat_completions_create_with_http_info(
    *,
    settings: Settings,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
    extra_headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str], int]:
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

    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.openai_timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    attempts = max(0, int(settings.openai_max_retries)) + 1
    retry_initial = float(settings.openai_retry_initial_seconds)
    retry_max = float(settings.openai_retry_max_seconds)

    started = time.monotonic()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if (
                resp.status_code >= 400
                and _is_retryable_status(resp.status_code)
                and attempt < attempts
            ):
                logger.warning(
                    "openai_retryable_status",
                    extra={
                        "status_code": resp.status_code,
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            resp.raise_for_status()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return resp.json(), {k: v for k, v in resp.headers.items()}, elapsed_ms
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < attempts and _is_retryable_exception(exc):
                logger.warning(
                    "openai_retryable_error",
                    extra={
                        "error": repr(exc),
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                )
                delay = min(retry_max, retry_initial * (2 ** (attempt - 1)))
                delay = delay * (0.75 + random.random() * 0.5)
                time.sleep(delay)
                continue

            logger.exception("openai_http_error", extra={"error": repr(exc)})
            raise OpenAIError(f"OpenAI request failed: {exc}") from exc

    raise OpenAIError(f"OpenAI request failed: {last_exc!r}")


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


def create_chat_completion_text_allow_empty_with_http_info(
    *,
    settings: Settings,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    extra_headers: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, Any], dict[str, str], int]:
    data, headers, latency_ms = chat_completions_create_with_http_info(
        settings=settings,
        messages=messages,
        temperature=temperature,
        extra_headers=extra_headers,
    )
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise OpenAIError(f"Unexpected OpenAI response shape: {data}") from exc

    if not isinstance(content, str):
        raise OpenAIError(f"Unexpected OpenAI response content type: {type(content)!r}")

    text = content.strip()
    if not text:
        return None, data, headers, latency_ms
    return text, data, headers, latency_ms
