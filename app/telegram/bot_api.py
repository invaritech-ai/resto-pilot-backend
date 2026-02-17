"""Telegram Bot API client for sending messages."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import Any
import urllib.parse

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096

_OUTGOING_MESSAGE_SINK: ContextVar[callable[[int, str], None] | None] = ContextVar(
    "_OUTGOING_MESSAGE_SINK",
    default=None,
)


class TelegramFileError(Exception):
    """Base class for Telegram file errors."""

    pass


class TelegramSendMessageError(Exception):
    """Raised when sendMessage fails (sanitized; never includes bot token)."""

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


def _split_telegram_message_text(*, text: str, limit: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break

        # Prefer splitting at a newline or space to keep messages readable.
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if chunk:
            parts.append(chunk)

        remaining = remaining[split_at:].lstrip()

    return parts or [text[:limit]]


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
    sink = _OUTGOING_MESSAGE_SINK.get()
    if sink is not None:
        if not isinstance(text, str) or not text.strip():
            return None
        for part in _split_telegram_message_text(text=text, limit=TELEGRAM_MAX_MESSAGE_LEN):
            sink(chat_id, part)
        # Return a deterministic placeholder message_id for telemetry.
        return 1

    # Console sink for local/dev testing: chat_id=0 prints instead of calling Telegram.
    # Telegram chat IDs are never 0, so this is safe and explicit.
    if chat_id == 0:
        if not isinstance(text, str) or not text.strip():
            return None
        for part in _split_telegram_message_text(text=text, limit=TELEGRAM_MAX_MESSAGE_LEN):
            print(part, flush=True)  # noqa: T201
        return 1

    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    if not isinstance(text, str) or not text.strip():
        logger.warning("telegram_send_message_skipped_empty", extra={"chat_id": chat_id})
        return None

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

    message_id: int | None = None
    for part in _split_telegram_message_text(text=text, limit=TELEGRAM_MAX_MESSAGE_LEN):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": part,
        }

        try:
            response = httpx.post(url, json=payload, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            telegram_error_code: int | None = None
            telegram_description: str | None = None
            try:
                body = e.response.json()
                if isinstance(body, dict):
                    telegram_error_code = body.get("error_code")
                    telegram_description = body.get("description")
            except Exception:
                pass

            logger.error(
                "telegram_send_message_http_error",
                extra={
                    "chat_id": chat_id,
                    "status_code": e.response.status_code,
                    "telegram_error_code": telegram_error_code,
                    "telegram_description": telegram_description,
                },
            )
            raise TelegramSendMessageError(
                f"Telegram sendMessage failed (HTTP {e.response.status_code}): "
                f"{telegram_description or 'Bad Request'}"
            ) from None
        except httpx.HTTPError as e:
            logger.error(
                "telegram_send_message_http_error",
                extra={"chat_id": chat_id, "error": type(e).__name__},
            )
            raise TelegramSendMessageError(
                f"Telegram sendMessage failed due to network error: {type(e).__name__}"
            ) from None

        try:
            result = response.json()
        except Exception:
            logger.error(
                "telegram_send_message_bad_json",
                extra={"chat_id": chat_id, "status_code": response.status_code},
            )
            raise TelegramSendMessageError("Telegram sendMessage returned invalid JSON") from None

        if not result.get("ok"):
            logger.error(
                "telegram_send_message_failed",
                extra={
                    "chat_id": chat_id,
                    "error_code": result.get("error_code"),
                    "description": result.get("description"),
                },
            )
            raise TelegramSendMessageError(
                f"Telegram sendMessage failed: {result.get('description') or result!r}"
            ) from None

        candidate = (result.get("result") or {}).get("message_id")
        if isinstance(candidate, int):
            message_id = candidate
            logger.info(
                "telegram_message_sent",
                extra={"chat_id": chat_id, "message_id": message_id},
            )

    return message_id


@contextmanager
def capture_outgoing_messages() -> Any:
    """
    Capture outgoing bot messages within the current context.

    This is used by local dev side-channel APIs to exercise the full pipeline
    without calling the real Telegram network.
    """
    captured: list[dict[str, Any]] = []

    def _sink(chat_id: int, text: str) -> None:
        captured.append({"chat_id": chat_id, "text": text})

    token = _OUTGOING_MESSAGE_SINK.set(_sink)
    try:
        yield captured
    finally:
        _OUTGOING_MESSAGE_SINK.reset(token)


def get_file_bytes_unified(
    *,
    file_id: str,
    settings: Settings,
    local_file_path: str | None = None,
    max_bytes: int = 20_000_000,
) -> bytes:
    """
    Download file bytes from either local path or Telegram.

    This unified function ensures identical behavior for test and production:
    - If local_file_path is provided, read from local disk
    - Otherwise, download from Telegram

    Both paths use identical error handling and size limits.

    Args:
        file_id: Telegram file identifier (used if local_file_path not provided)
        settings: Application settings containing bot token
        local_file_path: Optional local file path (bypasses Telegram download)
        max_bytes: Maximum file size in bytes (default 20MB)

    Returns:
        File bytes

    Raises:
        TelegramFileExpiredError: File not found/accessible
        TelegramFileTooBigError: File exceeds max_bytes limit
        TelegramFileNetworkError: Download/read error
    """
    if local_file_path:
        return get_file_bytes_from_local_path(file_path=local_file_path, max_bytes=max_bytes)
    else:
        return get_file_bytes(file_id=file_id, settings=settings, max_bytes=max_bytes)


def get_file_bytes_from_local_path(*, file_path: str, max_bytes: int = 20_000_000) -> bytes:
    """
    Read a local file with the same error handling as Telegram file downloads.

    This is the exact replica of get_file_bytes() but for local files,
    ensuring identical behavior for test and production.

    Args:
        file_path: Absolute path to local file
        max_bytes: Maximum file size in bytes (default 20MB)

    Returns:
        File bytes

    Raises:
        TelegramFileExpiredError: File not found/accessible (matches Telegram 404)
        TelegramFileTooBigError: File exceeds max_bytes limit
        TelegramFileNetworkError: File read error (matches Telegram network errors)
    """
    import os

    # Check file exists (equivalent to Telegram 404)
    if not os.path.isfile(file_path):
        raise TelegramFileExpiredError(
            f"Local file not found: {file_path}. This matches Telegram's behavior for expired files."
        )

    # Check file is readable (equivalent to Telegram 403)
    if not os.access(file_path, os.R_OK):
        raise TelegramFileExpiredError(
            f"Cannot read local file: {file_path}. This matches Telegram's permission errors."
        )

    # Read file with size limit checking (matches Telegram streaming behavior)
    try:
        chunks: list[bytes] = []
        total = 0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)  # Read in 8KB chunks like httpx
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise TelegramFileTooBigError(
                        f"File is too large ({total / 1024 / 1024:.1f}MB). "
                        f"Maximum size is {max_bytes / 1024 / 1024:.0f}MB."
                    )
                chunks.append(chunk)
        return b"".join(chunks)
    except (IOError, OSError) as e:
        # File read errors (equivalent to Telegram network errors)
        raise TelegramFileNetworkError(
            f"Error reading local file: {type(e).__name__}"
        ) from None


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
            ) from None
        elif e.response.status_code == 403:
            # Forbidden - bot doesn't have access
            raise TelegramFileExpiredError(
                "Bot doesn't have permission to access this file. Please re-upload."
            ) from None
        else:
            # 5xx or other errors - likely transient
            raise TelegramFileNetworkError(
                f"Temporary error downloading file (HTTP {e.response.status_code}). "
                "Please try again in a moment."
            ) from None
    except httpx.TimeoutException as e:
        raise TelegramFileNetworkError(
            "Timeout downloading file from Telegram. Please try again."
        ) from None
    except httpx.HTTPError as e:
        # Other network errors
        raise TelegramFileNetworkError(
            f"Network error downloading file: {type(e).__name__}"
        ) from None

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
            ) from None
        else:
            raise TelegramFileNetworkError(
                f"Error downloading file (HTTP {e.response.status_code})"
            ) from None
    except httpx.TimeoutException as e:
        raise TelegramFileNetworkError(
            "Timeout downloading file from Telegram. Please try again."
        ) from None
    except httpx.HTTPError as e:
        raise TelegramFileNetworkError(
            f"Network error downloading file: {type(e).__name__}"
        ) from None
