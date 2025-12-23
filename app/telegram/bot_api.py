"""Telegram Bot API client for sending messages."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


def send_message(chat_id: int, text: str, settings: Settings) -> None:
    """
    Send a text message to a Telegram chat via the Bot API.

    Args:
        chat_id: The Telegram chat ID to send the message to
        text: The message text to send
        settings: Application settings containing the bot token

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
        else:
            logger.info(
                "telegram_message_sent",
                extra={"chat_id": chat_id, "message_id": result.get("result", {}).get("message_id")},
            )
    except httpx.HTTPError as e:
        logger.error(
            "telegram_send_message_http_error",
            extra={"chat_id": chat_id, "error": str(e)},
        )
        raise
