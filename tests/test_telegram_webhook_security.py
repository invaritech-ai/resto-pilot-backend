from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.main import create_app


def _make_test_client(*, settings: Settings) -> TestClient:
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    return TestClient(app)


def test_telegram_webhook_returns_503_if_secret_not_configured():
    client = _make_test_client(
        settings=Settings(
            telegram_webhook_secret_token="",
            telegram_batching_enabled=False,
            celery_broker_url="redis://localhost",
        )
    )
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
        json={"update_id": 1},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Telegram webhook is not configured"


def test_telegram_webhook_returns_503_if_broker_not_configured():
    client = _make_test_client(
        settings=Settings(
            telegram_webhook_secret_token="secret",
            telegram_batching_enabled=False,
            celery_broker_url="",
        )
    )
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json={"update_id": 1},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Message broker is not configured"


def test_telegram_webhook_returns_401_if_secret_header_missing():
    client = _make_test_client(
        settings=Settings(
            telegram_webhook_secret_token="secret",
            telegram_batching_enabled=False,
            celery_broker_url="redis://localhost",
        )
    )
    resp = client.post("/api/v1/telegram", json={"update_id": 1})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing Telegram webhook secret"


def test_telegram_webhook_returns_403_if_secret_header_invalid():
    client = _make_test_client(
        settings=Settings(
            telegram_webhook_secret_token="secret",
            telegram_batching_enabled=False,
            celery_broker_url="redis://localhost",
        )
    )
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 1},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Invalid Telegram webhook secret"


def test_telegram_webhook_enqueues_update_when_secret_matches(monkeypatch: pytest.MonkeyPatch):
    client = _make_test_client(
        settings=Settings(
            telegram_webhook_secret_token="secret",
            telegram_batching_enabled=False,
            celery_broker_url="redis://localhost",
        )
    )

    enqueued: dict[str, object] = {}

    class FakeDelayResult:
        pass

    def _fake_delay(update: dict):
        enqueued["update"] = update
        return FakeDelayResult()

    from app.api.v1.routes import telegram as telegram_route

    monkeypatch.setattr(telegram_route.handle_telegram_update, "delay", _fake_delay)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json={"update_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert enqueued["update"] == {"update_id": 1}
