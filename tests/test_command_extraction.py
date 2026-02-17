import pytest

from app.telegram.commands import extract_command


@pytest.mark.parametrize(
    ("text", "caption", "expected"),
    [
        (None, None, (None, None)),
        ("", None, (None, None)),
        ("  ", "/inventory", ("/inventory", None)),
        ("/inventory", None, ("/inventory", None)),
        (None, "/inventory", ("/inventory", None)),
        ("/start CODE", None, ("/start", "CODE")),
        (None, "/start CODE", ("/start", "CODE")),
        ("/start@MyBot CODE", None, ("/start", "CODE")),
        ("hello", "/inventory", (None, None)),
    ],
)
def test_extract_command(text: str | None, caption: str | None, expected: tuple[str | None, str | None]):
    assert extract_command(text, caption) == expected
