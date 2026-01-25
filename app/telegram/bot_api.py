"""Telegram Bot API client for sending messages."""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class TelegramFileError(Exception):
    """Base class for Telegram file errors."""

    pass


class TelegramFileExpiredError(TelegramFileError):
    """File_id has expired or is no longer available on Telegram."""

    pass


class TelegramFileTooBigError(TelegramFileError):
    """File exceeds size limit."""

    pass


class TelegramFileNetworkError(TelegramFileError):
    """Transient network error, retry may succeed."""

    pass


def send_message(chat_id: int, text: str, settings: Settings) -> int | None:
    """
    Send a text message to a Telegram chat via the Bot API.

    Args:
        chat_id: The Telegram chat ID to send the message to
        text: The message text to send
        settings: Application settings containing the bot token

    Returns:
        Telegram message_id when available; otherwise None.

    Raises:
        httpx.HTTPError: If the API request fails
        ValueError: If the bot token is not configured
    """
    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        
        result = response.json()
        if not result.get("ok"):
            logger.error(
                "telegram_send_message_failed",
                extra={
                    "chat_id": chat_id,
                    "error_code": result.get("error_code"),
                    "description": result.get("description"),
                },
            )
            return None
        else:
            message_id = (result.get("result") or {}).get("message_id")
            logger.info(
                "telegram_message_sent",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            return message_id if isinstance(message_id, int) else None
    except httpx.HTTPError as e:
        logger.error(
            "telegram_send_message_http_error",
            extra={"chat_id": chat_id, "error": str(e)},
        )
        raise


def get_file_bytes(*, file_id: str, settings: Settings, max_bytes: int = 20_000_000) -> bytes:
    """
    Download a Telegram file by file_id with specific error handling.

    Raises specific exceptions for different failure modes to enable
    user-friendly error messages.

    Args:
        file_id: Telegram file identifier
        settings: Application settings containing bot token
        max_bytes: Maximum file size in bytes (default 20MB)

    Returns:
        File bytes

    Raises:
        TelegramFileExpiredError: File no longer available (404/403)
        TelegramFileTooBigError: File exceeds max_bytes limit
        TelegramFileNetworkError: Transient network/timeout error
        ValueError: Bot token not configured
    """
    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    file_id_q = urllib.parse.quote(file_id, safe="")
    get_file_url = (
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile?file_id={file_id_q}"
    )

    # Step 1: Get file path from Telegram
    try:
        response = httpx.get(get_file_url, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            # Bad Request or Not Found - file_id invalid/expired
            raise TelegramFileExpiredError(
                f"File is no longer available on Telegram (error {e.response.status_code}). "
                "Telegram files expire after 24-48 hours. Please re-upload the file."
            ) from e
        elif e.response.status_code == 403:
            # Forbidden - bot doesn't have access
            raise TelegramFileExpiredError(
                "Bot doesn't have permission to access this file. Please re-upload."
            ) from e
        else:
            # 5xx or other errors - likely transient
            raise TelegramFileNetworkError(
                f"Temporary error downloading file (HTTP {e.response.status_code}). "
                "Please try again in a moment."
            ) from e
    except httpx.TimeoutException as e:
        raise TelegramFileNetworkError(
            "Timeout downloading file from Telegram. Please try again."
        ) from e
    except httpx.HTTPError as e:
        # Other network errors
        raise TelegramFileNetworkError(
            f"Network error downloading file: {type(e).__name__}"
        ) from e

    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(f"Telegram getFile failed: {payload}")

    file_path = (payload.get("result") or {}).get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise ValueError(f"Telegram getFile missing file_path: {payload}")

    # Step 2: Download file bytes
    download_url = (
        f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    )

    try:
        with httpx.stream("GET", download_url, timeout=30.0) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise TelegramFileTooBigError(
                        f"File is too large ({total / 1024 / 1024:.1f}MB). "
                        f"Maximum size is {max_bytes / 1024 / 1024:.0f}MB."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 403):
            raise TelegramFileExpiredError(
                "File is no longer available on Telegram. Please re-upload."
            ) from e
        else:
            raise TelegramFileNetworkError(
                f"Error downloading file (HTTP {e.response.status_code})"
            ) from e
    except httpx.TimeoutException as e:
        raise TelegramFileNetworkError(
            "Timeout downloading file from Telegram. Please try again."
        ) from e
    except httpx.HTTPError as e:
        raise TelegramFileNetworkError(
            f"Network error downloading file: {type(e).__name__}"
        ) from e
