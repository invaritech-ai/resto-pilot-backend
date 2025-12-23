from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.main import create_app


def _make_test_client(*, settings: Settings) -> TestClient:
    app = create_app(settings=settings)

    def _db_override() -> Generator[object, None, None]:
        yield object()

    app.dependency_overrides[get_db_dep] = _db_override
    app.dependency_overrides[get_settings_dep] = lambda: settings
    return TestClient(app)


def test_telegram_webhook_returns_503_if_secret_not_configured():
    client = _make_test_client(
        settings=Settings(telegram_webhook_secret_token="", telegram_batching_enabled=False)
    )
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
        json={"update_id": 1},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Telegram webhook is not configured"


def test_telegram_webhook_returns_401_if_secret_header_missing():
    client = _make_test_client(
        settings=Settings(telegram_webhook_secret_token="secret", telegram_batching_enabled=False)
    )
    resp = client.post("/api/v1/telegram", json={"update_id": 1})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing Telegram webhook secret"


def test_telegram_webhook_returns_403_if_secret_header_invalid():
    client = _make_test_client(
        settings=Settings(telegram_webhook_secret_token="secret", telegram_batching_enabled=False)
    )
    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 1},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Invalid Telegram webhook secret"


def test_telegram_webhook_processes_update_when_secret_matches(monkeypatch: pytest.MonkeyPatch):
    client = _make_test_client(
        settings=Settings(telegram_webhook_secret_token="secret", telegram_batching_enabled=False)
    )

    called: dict[str, object] = {}

    def _fake_process_update(*, update: dict, session: object, settings: Settings):
        called["update"] = update
        called["settings"] = settings
        return None

    from app.api.v1.routes import telegram as telegram_route

    monkeypatch.setattr(telegram_route, "process_update", _fake_process_update)

    resp = client.post(
        "/api/v1/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json={"update_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert called["update"] == {"update_id": 1}
