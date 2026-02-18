"""Unit tests for app/llm/item_parser.py.

All LLM calls are mocked via unittest.mock — no network required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.llm.item_parser import ParseError, ocr_page_to_markdown, parse_invoice, parse_price_list


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_settings(
    vision_api_key: str = "test-key",
    vision_base_url: str | None = None,
    vision_model: str = "gpt-4o",
    openai_api_key: str = "fallback-key",
    openai_base_url: str | None = None,
    openai_model: str = "gpt-4o",
    openai_timeout_seconds: float = 30.0,
    openai_max_retries: int = 2,
    parser_model: str = "",
    parser_api_key: str = "",
    parser_base_url: str = "",
) -> MagicMock:
    s = MagicMock()
    s.vision_api_key = vision_api_key
    s.vision_base_url = vision_base_url
    s.vision_model = vision_model
    s.openai_api_key = openai_api_key
    s.openai_base_url = openai_base_url
    s.openai_model = openai_model
    s.openai_timeout_seconds = openai_timeout_seconds
    s.openai_max_retries = openai_max_retries
    s.parser_model = parser_model
    s.parser_api_key = parser_api_key
    s.parser_base_url = parser_base_url
    return s


def _mock_llm_response(content: str) -> MagicMock:
    """Build a mock OpenAI completions response with the given content."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


_INVOICE_PAYLOAD = {
    "supplier": "ACME Foods",
    "supplier_contact_name": "John",
    "supplier_phone": "+852 1234 5678",
    "supplier_email": "john@acme.com",
    "invoice_date": "2024-01-15",
    "invoice_number": "INV-001",
    "currency": "HKD",
    "line_items": [
        {"name": "Chicken Breast", "qty": 10.0, "unit": "kg", "unit_price": 45.0, "amount": 450.0},
        {"name": "Olive Oil", "qty": 2.0, "unit": "L", "unit_price": 80.0, "amount": 160.0},
    ],
}

_PRICE_LIST_PAYLOAD = {
    "supplier": "Fresh Farms",
    "supplier_contact_name": None,
    "supplier_phone": None,
    "supplier_email": None,
    "lead_time": "2-3 business days",
    "effective_date": "2024-02-01",
    "currency": "HKD",
    "line_items": [
        {"name": "Tomato", "unit": "kg", "unit_price": 12.5},
        {"name": "Lettuce", "unit": "pc", "unit_price": 8.0},
    ],
}


# ---------------------------------------------------------------------------
# parse_invoice — text input
# ---------------------------------------------------------------------------


class TestParseInvoiceText:
    def test_returns_structured_data(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_INVOICE_PAYLOAD)
            )

            result = parse_invoice(settings, text="Invoice text here")

        assert result["supplier"] == "ACME Foods"
        assert result["invoice_number"] == "INV-001"
        assert result["currency"] == "HKD"
        assert len(result["line_items"]) == 2

    def test_line_item_fields(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_INVOICE_PAYLOAD)
            )
            result = parse_invoice(settings, text="Invoice text")

        item = result["line_items"][0]
        assert item["name"] == "Chicken Breast"
        assert item["qty"] == 10.0
        assert item["unit"] == "kg"
        assert item["unit_price"] == 45.0
        assert item["amount"] == 450.0

    def test_strips_markdown_fences(self):
        settings = _make_settings()
        fenced = f"```json\n{json.dumps(_INVOICE_PAYLOAD)}\n```"
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(fenced)

            result = parse_invoice(settings, text="Invoice text")

        assert result["supplier"] == "ACME Foods"

    def test_strips_plain_code_fence(self):
        settings = _make_settings()
        fenced = f"```\n{json.dumps(_INVOICE_PAYLOAD)}\n```"
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(fenced)

            result = parse_invoice(settings, text="Invoice text")

        assert len(result["line_items"]) == 2

    def test_raises_parse_error_on_bad_json(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                "This is not JSON at all"
            )

            with pytest.raises(ParseError, match="invalid JSON"):
                parse_invoice(settings, text="Invoice text")

    def test_raises_when_no_line_items_key(self):
        settings = _make_settings()
        payload = {"supplier": "ACME"}  # Missing line_items
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )

            with pytest.raises(ParseError, match="line_items"):
                parse_invoice(settings, text="Invoice text")

    def test_skips_items_with_missing_qty(self):
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["line_items"] = [
            {"name": "Chicken", "qty": None, "unit": "kg", "unit_price": 45.0},
            {"name": "Oil", "qty": 2.0, "unit": "L", "unit_price": 80.0, "amount": 160.0},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="Invoice text")

        # Only Oil should survive (Chicken has no qty)
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["name"] == "Oil"

    def test_zero_unit_price_included_as_null(self):
        """Items with zero/missing unit_price are kept with unit_price=None for user review."""
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["line_items"] = [
            {"name": "Free Sample", "qty": 1.0, "unit": "pc", "unit_price": 0.0},
            {"name": "Chicken", "qty": 5.0, "unit": "kg", "unit_price": 50.0},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="Invoice text")

        assert len(result["line_items"]) == 2
        sample = next(i for i in result["line_items"] if i["name"] == "Free Sample")
        assert sample["unit_price"] is None

    def test_skips_items_with_no_name(self):
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["line_items"] = [
            {"name": "", "qty": 1.0, "unit": "kg", "unit_price": 10.0},
            {"name": "Tomato", "qty": 3.0, "unit": "kg", "unit_price": 12.0},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="Invoice text")

        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["name"] == "Tomato"

    def test_raises_parse_error_on_llm_exception(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.side_effect = RuntimeError("network error")

            with pytest.raises(ParseError, match="LLM call failed"):
                parse_invoice(settings, text="Invoice text")

    def test_null_amount_allowed(self):
        """amount field is optional in invoice items."""
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["line_items"] = [
            {"name": "Chicken", "qty": 5.0, "unit": "kg", "unit_price": 50.0, "amount": None},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="Invoice text")

        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["amount"] is None

    def test_string_numbers_coerced_to_float(self):
        """LLM sometimes returns numbers as strings."""
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["line_items"] = [
            {"name": "Rice", "qty": "10", "unit": "kg", "unit_price": "25.50"},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="Invoice text")

        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["qty"] == 10.0
        assert result["line_items"][0]["unit_price"] == 25.5


# ---------------------------------------------------------------------------
# parse_price_list — text input
# ---------------------------------------------------------------------------


class TestParsePriceListText:
    def test_returns_structured_data(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_PRICE_LIST_PAYLOAD)
            )
            result = parse_price_list(settings, text="Price list text")

        assert result["supplier"] == "Fresh Farms"
        assert result["lead_time"] == "2-3 business days"
        assert result["effective_date"] == "2024-02-01"
        assert len(result["line_items"]) == 2

    def test_price_list_items_have_no_qty(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_PRICE_LIST_PAYLOAD)
            )
            result = parse_price_list(settings, text="Price list text")

        item = result["line_items"][0]
        assert "qty" not in item
        assert item["name"] == "Tomato"
        assert item["unit_price"] == 12.5

    def test_zero_unit_price_included_as_null(self):
        """Items with zero/missing unit_price are kept with unit_price=None for user review."""
        settings = _make_settings()
        payload = dict(_PRICE_LIST_PAYLOAD)
        payload["line_items"] = [
            {"name": "Seasonal Item", "unit": "kg", "unit_price": 0.0},
            {"name": "Basil", "unit": "bunch", "unit_price": 5.0},
        ]
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_price_list(settings, text="Price list text")

        assert len(result["line_items"]) == 2
        seasonal = next(i for i in result["line_items"] if i["name"] == "Seasonal Item")
        assert seasonal["unit_price"] is None

    def test_strips_markdown_fences(self):
        settings = _make_settings()
        fenced = f"```json\n{json.dumps(_PRICE_LIST_PAYLOAD)}\n```"
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(fenced)
            result = parse_price_list(settings, text="Price list text")

        assert result["supplier"] == "Fresh Farms"

    def test_raises_on_bad_json(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                "Not valid JSON"
            )
            with pytest.raises(ParseError):
                parse_price_list(settings, text="Price list text")


# ---------------------------------------------------------------------------
# ocr_page_to_markdown — stage 1 vision OCR
# ---------------------------------------------------------------------------


class TestOcrPageToMarkdown:
    def test_returns_markdown_string(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                "## Invoice\n| Item | Price |\n|------|-------|\n| Chicken | 45.00 |"
            )
            result = ocr_page_to_markdown(settings, image_b64="base64data==")

        assert isinstance(result, str)
        assert "Chicken" in result

    def test_sends_image_url_content(self):
        """Vision call should include an image_url content block."""
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response("markdown")
            ocr_page_to_markdown(settings, image_b64="abc==", image_mime="image/png")

        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        content = messages[0]["content"]
        assert isinstance(content, list)
        types = [p["type"] for p in content]
        assert "image_url" in types
        image_part = next(p for p in content if p["type"] == "image_url")
        assert "image/png" in image_part["image_url"]["url"]

    def test_raises_parse_error_on_llm_failure(self):
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.side_effect = RuntimeError("timeout")
            with pytest.raises(ParseError, match="OCR LLM call failed"):
                ocr_page_to_markdown(settings, image_b64="abc==")

    def test_uses_vision_model(self):
        settings = _make_settings(vision_model="gemini-2.0-flash")
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response("text")
            ocr_page_to_markdown(settings, image_b64="abc==")

        call_args = client.chat.completions.create.call_args
        model = call_args.kwargs.get("model") or call_args[0][0]
        assert model == "gemini-2.0-flash"

    def test_parser_uses_separate_model(self):
        """parse_invoice stage 2 should use parser_model, not vision_model."""
        settings = _make_settings(
            vision_model="expensive-vision-model",
            parser_model="cheap-text-model",
        )
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_INVOICE_PAYLOAD)
            )
            parse_invoice(settings, text="invoice text")

        call_args = client.chat.completions.create.call_args
        model = call_args.kwargs.get("model") or call_args[0][0]
        assert model == "cheap-text-model"

    def test_parser_text_in_user_message(self):
        """Text content should appear in the user message (not system)."""
        settings = _make_settings()
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(_INVOICE_PAYLOAD)
            )
            parse_invoice(settings, text="Raw invoice text")

        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Raw invoice text" in user_msg["content"]


# ---------------------------------------------------------------------------
# Nullable top-level fields
# ---------------------------------------------------------------------------


class TestNullableFields:
    def test_null_supplier_preserved(self):
        settings = _make_settings()
        payload = dict(_INVOICE_PAYLOAD)
        payload["supplier"] = None
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="invoice text")

        assert result["supplier"] is None

    def test_missing_optional_field_is_none(self):
        settings = _make_settings()
        payload = {
            "supplier": "ACME",
            "line_items": [
                {"name": "Chicken", "qty": 5.0, "unit": "kg", "unit_price": 50.0}
            ],
        }
        with patch("app.llm.item_parser.OpenAI") as MockOpenAI:
            client = MockOpenAI.return_value
            client.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(payload)
            )
            result = parse_invoice(settings, text="invoice text")

        assert result.get("invoice_number") is None
        assert result.get("invoice_date") is None
        assert result.get("currency") is None
