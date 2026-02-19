"""Telegram Bot API client for sending messages."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import Any
import urllib.parse
import uuid

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096
_OUTLET_LABEL_MAX_CHARS = 12
_OUTLET_BADGE_FALLBACK = "-"
_OUTLET_BADGE_EMOJI = "🏬"

_OUTGOING_MESSAGE_SINK: ContextVar[Callable[[int, str], None] | None] = ContextVar(
    "_OUTGOING_MESSAGE_SINK",
    default=None,
)
_OUTGOING_DB_LOGGER: ContextVar[Callable[[int, str, int | None], None] | None] = ContextVar(
    "_OUTGOING_DB_LOGGER",
    default=None,
)
_CURRENT_SESSION_ID: ContextVar[uuid.UUID | None] = ContextVar(
    "_CURRENT_SESSION_ID",
    default=None,
)
_OUTLET_BADGE_LABEL: ContextVar[str | None] = ContextVar(
    "_OUTLET_BADGE_LABEL",
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


def _split_telegram_message_text(
    *, text: str, limit: int = TELEGRAM_MAX_MESSAGE_LEN
) -> list[str]:
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


def _format_outlet_label(raw_label: str | None) -> str:
    label = (raw_label or "").strip()
    if not label:
        return _OUTLET_BADGE_FALLBACK
    if len(label) <= _OUTLET_LABEL_MAX_CHARS:
        return label
    return f"{label[:_OUTLET_LABEL_MAX_CHARS - 1]}…"


def _decorate_with_outlet_badge(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text

    label_raw = _OUTLET_BADGE_LABEL.get()
    if label_raw is None:
        return text

    # Avoid duplicating the badge if callers already provided it.
    if text.startswith(f"{_OUTLET_BADGE_EMOJI} ") or text.startswith(
        f"{_OUTLET_BADGE_EMOJI}\n"
    ):
        return text

    label = _format_outlet_label(label_raw)
    full_badge = f"{_OUTLET_BADGE_EMOJI} {label}"
    decorated = f"{full_badge}\n{text}"
    if len(decorated) <= TELEGRAM_MAX_MESSAGE_LEN:
        return decorated

    compact_badge = f"{_OUTLET_BADGE_EMOJI}\n{text}"
    if len(compact_badge) <= TELEGRAM_MAX_MESSAGE_LEN:
        return compact_badge

    return decorated


def _log_outgoing_message(
    *,
    chat_id: int,
    text: str,
    telegram_message_id: int | None,
) -> None:
    db_logger = _OUTGOING_DB_LOGGER.get()
    if db_logger is None:
        return
    if not isinstance(text, str) or not text.strip():
        return
    try:
        db_logger(chat_id, text, telegram_message_id)
    except Exception:
        logger.exception(
            "telegram_outgoing_db_logger_failed",
            extra={
                "chat_id": chat_id,
                "telegram_message_id": telegram_message_id,
            },
        )


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
    if not isinstance(text, str) or not text.strip():
        logger.warning(
            "telegram_send_message_skipped_empty", extra={"chat_id": chat_id}
        )
        return None

    text_out = _decorate_with_outlet_badge(text)

    sink = _OUTGOING_MESSAGE_SINK.get()
    if sink is not None:
        for part in _split_telegram_message_text(
            text=text_out, limit=TELEGRAM_MAX_MESSAGE_LEN
        ):
            sink(chat_id, part)
            _log_outgoing_message(
                chat_id=chat_id,
                text=part,
                telegram_message_id=None,
            )
        # Return a deterministic placeholder message_id for telemetry.
        return 1

    # Console sink for local/dev testing: chat_id=0 prints instead of calling Telegram.
    # Telegram chat IDs are never 0, so this is safe and explicit.
    if chat_id == 0:
        for part in _split_telegram_message_text(
            text=text_out, limit=TELEGRAM_MAX_MESSAGE_LEN
        ):
            print(part, flush=True)  # noqa: T201
            _log_outgoing_message(
                chat_id=chat_id,
                text=part,
                telegram_message_id=None,
            )
        return 1

    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

    message_id: int | None = None
    for part in _split_telegram_message_text(
        text=text_out, limit=TELEGRAM_MAX_MESSAGE_LEN
    ):
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
            raise TelegramSendMessageError(
                "Telegram sendMessage returned invalid JSON"
            ) from None

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
        part_message_id: int | None = candidate if isinstance(candidate, int) else None
        if part_message_id is not None:
            message_id = part_message_id
            logger.info(
                "telegram_message_sent",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
        _log_outgoing_message(
            chat_id=chat_id,
            text=part,
            telegram_message_id=part_message_id,
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


@contextmanager
def bind_outgoing_db_logger(
    logger_fn: Callable[[int, str, int | None], None] | None,
) -> Any:
    """Bind outgoing-message DB logger for the current task context."""
    token = _OUTGOING_DB_LOGGER.set(logger_fn)
    try:
        yield
    finally:
        _OUTGOING_DB_LOGGER.reset(token)


@contextmanager
def bind_current_session(session_id: uuid.UUID | None) -> Any:
    """Bind current telegram session_id for the current task context."""
    token = _CURRENT_SESSION_ID.set(session_id)
    try:
        yield
    finally:
        _CURRENT_SESSION_ID.reset(token)


@contextmanager
def bind_outlet_badge(label: str | None) -> Any:
    """Bind outlet label for outgoing message decoration in this context."""
    token = _OUTLET_BADGE_LABEL.set(label)
    try:
        yield
    finally:
        _OUTLET_BADGE_LABEL.reset(token)


def get_current_session_id() -> uuid.UUID | None:
    """Return active telegram session_id from context, if set."""
    return _CURRENT_SESSION_ID.get()


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
        return get_file_bytes_from_local_path(
            file_path=local_file_path, max_bytes=max_bytes
        )
    else:
        return get_file_bytes(file_id=file_id, settings=settings, max_bytes=max_bytes)


def get_file_bytes_from_local_path(
    *, file_path: str, max_bytes: int = 20_000_000
) -> bytes:
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


def get_file_bytes(
    *, file_id: str, settings: Settings, max_bytes: int = 20_000_000
) -> bytes:
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
    get_file_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile?file_id={file_id_q}"

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


def send_message_with_keyboard(
    chat_id: int,
    text: str,
    reply_markup: dict,
    settings: Settings,
) -> int | None:
    """Send a message with an inline keyboard. Returns message_id or None.

    Falls back to plain send_message when in dev/test sink mode.
    """
    if not isinstance(text, str) or not text.strip():
        logger.warning(
            "telegram_send_message_with_keyboard_skipped_empty",
            extra={"chat_id": chat_id},
        )
        return None

    text_out = _decorate_with_outlet_badge(text)

    sink = _OUTGOING_MESSAGE_SINK.get()
    if sink is not None:
        sink(chat_id, text_out)
        _log_outgoing_message(chat_id=chat_id, text=text_out, telegram_message_id=None)
        return 1

    if chat_id == 0:
        print(text_out, flush=True)  # noqa: T201
        _log_outgoing_message(chat_id=chat_id, text=text_out, telegram_message_id=None)
        return 1

    if not settings.telegram_bot_token:
        return send_message(chat_id=chat_id, text=text_out, settings=settings)

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text_out,
        "reply_markup": reply_markup,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            msg_id = (data.get("result") or {}).get("message_id")
            telegram_message_id = int(msg_id) if msg_id else None
            _log_outgoing_message(
                chat_id=chat_id,
                text=text_out,
                telegram_message_id=telegram_message_id,
            )
            return telegram_message_id
        logger.error("send_message_with_keyboard failed: %s", data.get("description"))
        return None
    except Exception as exc:
        logger.error("send_message_with_keyboard error: %s", exc)
        return None


def answer_callback_query(
    callback_id: str,
    text: str = "",
    show_alert: bool = False,
    settings: Settings | None = None,
) -> bool:
    """Answer a callback query from an inline button press.

    Args:
        callback_id: The callback_query.id from the update
        text: Optional text to show to the user
        show_alert: If True, show as a popup alert instead of toast
        settings: Application settings containing the bot token

    Returns:
        True if successful, False otherwise
    """
    from app.core.config import get_settings

    if settings is None:
        settings = get_settings()

    if not settings.telegram_bot_token:
        logger.warning("answer_callback_query_skipped: no bot token")
        return False

    url = (
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    )

    payload: dict[str, Any] = {
        "callback_query_id": callback_id,
    }
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True

    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            logger.debug("callback_query_answered", extra={"callback_id": callback_id})
            return True
        else:
            logger.warning(
                "answer_callback_query_failed",
                extra={"callback_id": callback_id, "error": result.get("description")},
            )
            return False
    except httpx.HTTPError as e:
        logger.error(
            "answer_callback_query_http_error",
            extra={"callback_id": callback_id, "error": type(e).__name__},
        )
        return False


def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    reply_markup: dict | None,
    settings: Settings,
) -> bool:
    """Edit only an existing message's inline keyboard markup."""
    if not settings.telegram_bot_token:
        logger.warning("edit_message_reply_markup_skipped: no bot token")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageReplyMarkup"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup,
    }

    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            logger.debug(
                "message_reply_markup_edited",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            return True
        logger.warning(
            "edit_message_reply_markup_failed",
            extra={
                "chat_id": chat_id,
                "message_id": message_id,
                "error": result.get("description"),
            },
        )
        return False
    except httpx.HTTPError as e:
        logger.error(
            "edit_message_reply_markup_http_error",
            extra={
                "chat_id": chat_id,
                "message_id": message_id,
                "error": type(e).__name__,
            },
        )
        return False


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    settings: Settings,
    reply_markup: dict | None = None,
) -> bool:
    """Edit an existing message's text.

    Args:
        chat_id: The Telegram chat ID
        message_id: The message ID to edit
        text: The new text content
        settings: Application settings containing the bot token
        reply_markup: Optional inline keyboard markup

    Returns:
        True if successful, False otherwise
    """
    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    if not isinstance(text, str) or not text.strip():
        logger.warning("edit_message_text_skipped_empty", extra={"chat_id": chat_id})
        return False

    text_out = _decorate_with_outlet_badge(text)

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text_out,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            logger.debug(
                "message_edited",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            return True
        else:
            logger.warning(
                "edit_message_text_failed",
                extra={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "error": result.get("description"),
                },
            )
            return False
    except httpx.HTTPError as e:
        logger.error(
            "edit_message_text_http_error",
            extra={
                "chat_id": chat_id,
                "message_id": message_id,
                "error": type(e).__name__,
            },
        )
        return False


def send_document(
    chat_id: int,
    file_bytes: bytes,
    filename: str,
    caption: str | None = None,
    settings: Settings = None,  # type: ignore[assignment]
) -> None:
    """Send a file to a Telegram chat via sendDocument.

    Skipped silently in test/dev sink mode (no document delivery infrastructure).
    """
    if chat_id == 0:
        logger.info("send_document skipped (console mode) filename=%s", filename)
        return

    if not settings or not settings.telegram_bot_token:
        raise ValueError("Telegram bot token is not configured")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    try:
        resp = httpx.post(
            url,
            data={"chat_id": chat_id, **({"caption": caption} if caption else {})},
            files={"document": (filename, file_bytes, "application/octet-stream")},
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(
            "telegram_send_document_http_error",
            extra={"chat_id": chat_id, "filename": filename, "error": type(e).__name__},
        )
        raise
