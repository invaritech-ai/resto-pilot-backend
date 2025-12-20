from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from app.core.config import Settings


class TelegramInitDataError(ValueError):
    pass


def verify_telegram_webapp_init_data(
    *, init_data: str, settings: Settings, now: int | None = None
) -> dict[str, Any]:
    """
    Validate Telegram Mini App initData and return parsed fields.

    Telegram verification algorithm:
    - Remove 'hash' from the querystring params
    - Sort remaining params by key
    - Build data_check_string as "k=v" lines separated by "\n"
    - secret_key = hmac_sha256(key="WebAppData", msg=bot_token)
    - expected_hash = hmac_sha256(secret_key, data_check_string).hexdigest()
    """
    if not settings.telegram_bot_token:
        raise TelegramInitDataError("APP_TELEGRAM_BOT_TOKEN is not set")

    pairs = list(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise TelegramInitDataError("Missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(
        b"WebAppData",
        settings.telegram_bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramInitDataError("Invalid signature")

    auth_date_raw = data.get("auth_date")
    if auth_date_raw is None:
        raise TelegramInitDataError("Missing auth_date")

    try:
        auth_date = int(auth_date_raw)
    except ValueError as e:
        raise TelegramInitDataError("Invalid auth_date") from e

    now = now or int(time.time())
    if now - auth_date > settings.telegram_webapp_auth_max_age_seconds:
        raise TelegramInitDataError("initData expired")

    parsed: dict[str, Any] = dict(data)
    user_raw = parsed.get("user")
    if isinstance(user_raw, str):
        try:
            parsed["user"] = json.loads(user_raw)
        except json.JSONDecodeError as e:
            raise TelegramInitDataError("Invalid user JSON") from e

    return parsed


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


@dataclass(frozen=True)
class AccessTokenPayload:
    user_id: str
    telegram_id: int
    exp: int


class AccessTokenError(ValueError):
    pass


def encode_access_token(*, payload: AccessTokenPayload, settings: Settings) -> str:
    header = {"typ": "RP", "alg": "HS256"}
    body = {"sub": payload.user_id, "telegram_id": payload.telegram_id, "exp": payload.exp}
    signing_input = f"{_b64url_encode(json.dumps(header).encode())}.{_b64url_encode(json.dumps(body).encode())}"
    sig = hmac.new(
        settings.auth_secret.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def decode_access_token(*, token: str, settings: Settings, now: int | None = None) -> AccessTokenPayload:
    try:
        header_b64, body_b64, sig_b64 = token.split(".", 2)
    except ValueError as e:
        raise AccessTokenError("Invalid token format") from e

    signing_input = f"{header_b64}.{body_b64}"
    expected_sig = hmac.new(
        settings.auth_secret.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    try:
        received_sig = _b64url_decode(sig_b64)
    except Exception as e:
        raise AccessTokenError("Invalid signature encoding") from e

    if not hmac.compare_digest(expected_sig, received_sig):
        raise AccessTokenError("Invalid signature")

    try:
        body = json.loads(_b64url_decode(body_b64))
    except Exception as e:
        raise AccessTokenError("Invalid token payload") from e

    exp = body.get("exp")
    sub = body.get("sub")
    telegram_id = body.get("telegram_id")
    if not isinstance(exp, int) or not isinstance(sub, str) or not isinstance(telegram_id, int):
        raise AccessTokenError("Invalid token payload fields")

    now = now or int(time.time())
    if exp <= now:
        raise AccessTokenError("Token expired")

    return AccessTokenPayload(user_id=sub, telegram_id=telegram_id, exp=exp)
