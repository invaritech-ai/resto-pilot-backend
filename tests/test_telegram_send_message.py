import httpx
import pytest

from app.core.config import Settings
from app.telegram import bot_api


def _response(*, url: str, status_code: int, json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", url)
    return httpx.Response(status_code=status_code, json=json_body, request=request)


def test_send_message_skips_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(telegram_bot_token="TOKEN")

    def fake_post(*args, **kwargs):
        raise AssertionError("send_message should not call httpx.post for empty text")

    monkeypatch.setattr(bot_api.httpx, "post", fake_post)
    assert bot_api.send_message(chat_id=123, text="   ", settings=settings) is None


def test_send_message_splits_long_text(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(telegram_bot_token="TOKEN")
    sent_parts: list[str] = []

    def fake_post(url: str, json: dict, timeout: float) -> httpx.Response:
        sent_parts.append(json["text"])
        message_id = len(sent_parts)
        return _response(
            url=url,
            status_code=200,
            json_body={"ok": True, "result": {"message_id": message_id}},
        )

    monkeypatch.setattr(bot_api.httpx, "post", fake_post)

    text = "a" * (bot_api.TELEGRAM_MAX_MESSAGE_LEN * 2 + 10)
    message_id = bot_api.send_message(chat_id=123, text=text, settings=settings)

    assert message_id == 3
    assert len(sent_parts) == 3
    assert all(len(p) <= bot_api.TELEGRAM_MAX_MESSAGE_LEN for p in sent_parts)


def test_send_message_http_error_does_not_leak_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "SECRET_TOKEN"
    settings = Settings(telegram_bot_token=token)

    def fake_post(url: str, json: dict, timeout: float) -> httpx.Response:
        return _response(
            url=url,
            status_code=400,
            json_body={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )

    monkeypatch.setattr(bot_api.httpx, "post", fake_post)

    with pytest.raises(bot_api.TelegramSendMessageError) as exc:
        bot_api.send_message(chat_id=123, text="hi", settings=settings)

    assert token not in str(exc.value)

