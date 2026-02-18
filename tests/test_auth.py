"""Tests for security token functions and /auth/telegram-webapp endpoint."""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db_dep, get_settings_dep
from app.api.security import (
    AccessTokenError,
    AccessTokenPayload,
    decode_access_token,
    encode_access_token,
)
from app.core.config import Settings
from app.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    auth_secret: str = "test-secret-key",
    telegram_bot_token: str = "1234:FAKE",
    telegram_webhook_secret_token: str = "webhook-secret",
    celery_broker_url: str = "redis://localhost",
    auth_token_ttl_seconds: int = 3600,
) -> Settings:
    return Settings(
        auth_secret=auth_secret,
        telegram_bot_token=telegram_bot_token,
        telegram_webhook_secret_token=telegram_webhook_secret_token,
        celery_broker_url=celery_broker_url,
        auth_token_ttl_seconds=auth_token_ttl_seconds,
    )


def _valid_payload(*, ttl: int = 3600) -> AccessTokenPayload:
    return AccessTokenPayload(
        user_id=str(uuid.uuid4()),
        telegram_id=999,
        exp=int(time.time()) + ttl,
    )


# ---------------------------------------------------------------------------
# encode_access_token / decode_access_token
# ---------------------------------------------------------------------------


class TestEncodeDecodeAccessToken:
    def test_round_trip(self):
        settings = _make_settings()
        payload = _valid_payload()
        token = encode_access_token(payload=payload, settings=settings)
        decoded = decode_access_token(token=token, settings=settings)
        assert decoded.user_id == payload.user_id
        assert decoded.telegram_id == payload.telegram_id
        assert decoded.exp == payload.exp

    def test_encode_raises_when_secret_missing(self):
        settings = _make_settings(auth_secret="")
        with pytest.raises(AccessTokenError, match="APP_AUTH_SECRET"):
            encode_access_token(payload=_valid_payload(), settings=settings)

    def test_decode_raises_when_secret_missing(self):
        # Encode with a valid secret, then try to decode without one.
        settings = _make_settings()
        token = encode_access_token(payload=_valid_payload(), settings=settings)
        empty_settings = _make_settings(auth_secret="")
        with pytest.raises(AccessTokenError, match="APP_AUTH_SECRET"):
            decode_access_token(token=token, settings=empty_settings)

    def test_decode_raises_on_expired_token(self):
        settings = _make_settings()
        payload = _valid_payload(ttl=-1)  # already expired
        token = encode_access_token(payload=payload, settings=settings)
        with pytest.raises(AccessTokenError, match="expired"):
            decode_access_token(token=token, settings=settings)

    def test_decode_raises_on_tampered_signature(self):
        settings = _make_settings()
        token = encode_access_token(payload=_valid_payload(), settings=settings)
        # Flip the last character of the signature segment.
        parts = token.rsplit(".", 1)
        tampered = (
            parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        )
        with pytest.raises(AccessTokenError):
            decode_access_token(token=tampered, settings=settings)

    def test_decode_raises_on_malformed_token(self):
        settings = _make_settings()
        with pytest.raises(AccessTokenError):
            decode_access_token(token="not.a.valid.token.at.all", settings=settings)

    def test_different_secrets_produce_different_tokens(self):
        payload = _valid_payload()
        t1 = encode_access_token(
            payload=payload, settings=_make_settings(auth_secret="secret-a")
        )
        t2 = encode_access_token(
            payload=payload, settings=_make_settings(auth_secret="secret-b")
        )
        assert t1 != t2


# ---------------------------------------------------------------------------
# /auth/telegram-webapp endpoint
# ---------------------------------------------------------------------------


def _make_test_client(settings: Settings) -> TestClient:
    """Build a TestClient with a mocked DB session."""
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings

    # Provide a no-op DB session so the endpoint doesn't need a real database.
    mock_db = MagicMock()
    app.dependency_overrides[get_db_dep] = lambda: mock_db

    return TestClient(app, raise_server_exceptions=False)


class TestTelegramWebappAuthEndpoint:
    def test_returns_400_when_init_data_missing(self):
        client = _make_test_client(_make_settings())
        resp = client.post("/api/v1/auth/telegram-webapp", json={})
        assert resp.status_code == 400
        assert "initData" in resp.json()["detail"]

    def test_returns_401_when_init_data_invalid(self):
        client = _make_test_client(_make_settings())
        resp = client.post(
            "/api/v1/auth/telegram-webapp",
            json={"initData": "auth_date=1234&user=%7B%22id%22%3A1%7D&hash=badhash"},
        )
        assert resp.status_code == 401

    def test_returns_503_when_auth_secret_missing(self):
        """encode_access_token raises AccessTokenError → must surface as 503, not 500."""
        settings = _make_settings(auth_secret="")

        # Patch verify so we skip real Telegram crypto and focus on the 503 path.
        fake_user = {"id": 12345, "first_name": "Test", "username": "testuser"}
        fake_parsed = {"user": fake_user}

        db_user = MagicMock()
        db_user.id = uuid.uuid4()
        db_user.telegram_id = 12345
        db_user.full_name = "Test User"

        mock_db = MagicMock()
        mock_db.commit = MagicMock()

        with (
            patch(
                "app.api.v1.routes.auth.verify_telegram_webapp_init_data",
                return_value=fake_parsed,
            ),
            patch("app.api.v1.routes.auth.UserService") as MockUserSvc,
        ):
            MockUserSvc.return_value.get_or_create.return_value = (db_user, False)

            app = create_app(settings=settings)
            app.dependency_overrides[get_settings_dep] = lambda: settings
            app.dependency_overrides[get_db_dep] = lambda: mock_db

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/telegram-webapp",
                json={"initData": "auth_date=1234&hash=anything"},
            )

        assert resp.status_code == 503
        assert "misconfigured" in resp.json()["detail"].lower()

    def test_returns_token_on_success(self):
        """Happy path: valid parsed data + working secret → returns access_token."""
        settings = _make_settings()

        fake_user = {"id": 12345, "first_name": "Test", "username": "testuser"}
        fake_parsed = {"user": fake_user}

        db_user = MagicMock()
        db_user.id = uuid.uuid4()
        db_user.telegram_id = 12345
        db_user.full_name = "Test User"

        mock_db = MagicMock()
        mock_db.commit = MagicMock()

        with (
            patch(
                "app.api.v1.routes.auth.verify_telegram_webapp_init_data",
                return_value=fake_parsed,
            ),
            patch("app.api.v1.routes.auth.UserService") as MockUserSvc,
            patch(
                "app.schemas.user.UserRead.model_validate",
                return_value={"id": str(db_user.id), "telegram_id": 12345},
            ),
        ):
            MockUserSvc.return_value.get_or_create.return_value = (db_user, False)

            app = create_app(settings=settings)
            app.dependency_overrides[get_settings_dep] = lambda: settings
            app.dependency_overrides[get_db_dep] = lambda: mock_db

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/telegram-webapp",
                json={"initData": "auth_date=1234&hash=anything"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

        # Verify the token is actually decodable with the same secret.
        decoded = decode_access_token(token=body["access_token"], settings=settings)
        assert decoded.telegram_id == 12345
        assert decoded.user_id == str(db_user.id)
