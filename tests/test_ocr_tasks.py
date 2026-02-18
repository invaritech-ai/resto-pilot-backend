"""Unit tests for app/workers/ocr_tasks.py.

All external calls (Telegram file download, LLM, SupplierService, httpx) are mocked.
Celery is called synchronously by invoking process_file_task directly (no broker needed).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STAGING_ID = uuid.uuid4()
RESTAURANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
SUPPLIER_ID = uuid.uuid4()

_INVOICE_EXTRACTED = {
    "supplier": "ACME Foods",
    "invoice_date": "2024-01-15",
    "invoice_number": "INV-001",
    "currency": "HKD",
    "line_items": [
        {"name": "Chicken Breast", "qty": 10.0, "unit": "kg", "unit_price": 45.0, "amount": 450.0},
        {"name": "Olive Oil", "qty": 2.0, "unit": "L", "unit_price": 80.0, "amount": 160.0},
    ],
}

_PRICE_LIST_EXTRACTED = {
    "supplier": "Fresh Farms",
    "lead_time": "2 days",
    "effective_date": "2024-02-01",
    "currency": "HKD",
    "line_items": [
        {"name": "Tomato", "unit": "kg", "unit_price": 12.5},
        {"name": "Lettuce", "unit": "pc", "unit_price": 8.0},
    ],
}


def _make_staging(
    staging_id: uuid.UUID | None = None,
    file_id: str = "tg_file_123",
    mime: str = "application/pdf",
    document_type: str = "invoice",
    restaurant_id: uuid.UUID | None = None,
) -> MagicMock:
    s = MagicMock()
    s.id = staging_id or STAGING_ID
    s.file_id = file_id
    s.mime = mime
    s.document_type = document_type
    s.restaurant_id = restaurant_id or RESTAURANT_ID
    s.supplier_id = None
    return s


def _make_supplier(name: str = "ACME Foods") -> MagicMock:
    sup = MagicMock()
    sup.id = SUPPLIER_ID
    sup.name = name
    return sup


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = "faketoken"
    s.vision_api_key = "key"
    s.vision_base_url = None
    s.vision_model = "gpt-4o"
    s.vision_pdf_chunk_size = 3
    return s


def _run_task(staging_id_hex: str, document_type: str, chat_id: int = 99999) -> None:
    """Import and run process_file_task directly (bypasses Celery broker)."""
    from app.workers.ocr_tasks import process_file_task
    process_file_task(staging_id_hex, document_type, chat_id)


# ---------------------------------------------------------------------------
# Helper to build a patched DB session that returns our staging
# ---------------------------------------------------------------------------

def _patch_db_and_task(staging: MagicMock):
    """Context manager patches for the happy-path:
      - worker_db_session → yields mocked db
      - db.get → returns staging
    """
    from contextlib import contextmanager

    db = MagicMock()
    db.get.return_value = staging
    db.flush = MagicMock()
    db.commit = MagicMock()

    @contextmanager
    def _fake_db_session():
        yield db

    return db, _fake_db_session


# ---------------------------------------------------------------------------
# Invalid staging_id_hex
# ---------------------------------------------------------------------------


class TestInvalidInputs:
    def test_invalid_staging_hex_returns_early(self):
        """If staging_id_hex is not a valid UUID hex, task should return silently."""
        with patch("app.workers.ocr_tasks.worker_db_session") as mock_ctx:
            _run_task("not-a-valid-hex", "invoice")
            # worker_db_session should never have been entered
            mock_ctx.assert_not_called()

    def test_staging_not_found_returns_early(self):
        """If staging record not in DB, task returns without error."""
        db = MagicMock()
        db.get.return_value = None
        db.__enter__ = MagicMock(return_value=db)
        db.__exit__ = MagicMock(return_value=False)

        from contextlib import contextmanager

        @contextmanager
        def _fake_db():
            yield db

        with patch("app.workers.ocr_tasks.worker_db_session", _fake_db):
            with patch("app.workers.ocr_tasks.get_settings", return_value=_make_settings()):
                _run_task(STAGING_ID.hex, "invoice")

        # Did not crash, db.get was called, no further processing
        db.get.assert_called_once()


# ---------------------------------------------------------------------------
# Supplier gate: ≥ 0.8 auto-match
# ---------------------------------------------------------------------------


class TestSupplierGateAutoMatch:
    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_auto_match_sets_supplier_on_staging(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)
        mock_db_ctx.return_value = fake_ctx()

        supplier = _make_supplier("ACME Foods")
        mock_resolve.return_value = (supplier, None)  # auto-match, no buttons
        mock_extract.return_value = _INVOICE_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 42  # message_id

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        # supplier_id should be set on staging
        assert staging.supplier_id == supplier.id

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_auto_match_sends_review_message(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        supplier = _make_supplier("ACME Foods")
        mock_resolve.return_value = (supplier, None)
        mock_extract.return_value = _INVOICE_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 99

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        # _send_with_keyboard should be called once
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        # chat_id is passed
        assert call_args[0][0] == 99999 or call_args[1].get("chat_id") == 99999


# ---------------------------------------------------------------------------
# Supplier gate: 0.5–0.8 suggest
# ---------------------------------------------------------------------------


class TestSupplierGateSuggest:
    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_suggest_does_not_auto_set_supplier(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        # 0.5–0.8: no auto supplier, but buttons provided
        suggest_buttons = [
            {"text": "✅ ACME Foods", "callback_data": "set_sup:abc:def"},
            {"text": "➕ Create 'ACME Foods'", "callback_data": "new_sup:abc"},
        ]
        mock_resolve.return_value = (None, suggest_buttons)
        mock_extract.return_value = _INVOICE_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 77

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        # supplier_id should NOT be set
        assert staging.supplier_id is None

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_suggest_includes_buttons_in_keyboard(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        suggest_buttons = [{"text": "✅ ACME", "callback_data": "set_sup:abc:def"}]
        mock_resolve.return_value = (None, suggest_buttons)
        mock_extract.return_value = _INVOICE_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 77

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            with patch("app.workers.ocr_tasks._build_review_keyboard") as mock_kboard:
                mock_kboard.return_value = {"inline_keyboard": []}
                _run_task(STAGING_ID.hex, "invoice")

        # _build_review_keyboard was called with sup_buttons
        mock_kboard.assert_called_once()
        call_kwargs = mock_kboard.call_args
        sup_arg = call_kwargs[0][2] if len(call_kwargs[0]) >= 3 else call_kwargs[1].get("sup_buttons")
        assert sup_arg == suggest_buttons


# ---------------------------------------------------------------------------
# Supplier gate: < 0.5 new
# ---------------------------------------------------------------------------


class TestSupplierGateNew:
    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_no_match_shows_create_button(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        # No match at all — just "Create new" button
        create_button = [{"text": "➕ Create 'ACME Foods'", "callback_data": "new_sup:abc"}]
        mock_resolve.return_value = (None, create_button)
        mock_extract.return_value = _INVOICE_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 55

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        # No auto-supplier set
        assert staging.supplier_id is None
        # Send called once
        mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks.StagingService")
    @patch("app.workers.ocr_tasks.send_message")
    def test_telegram_file_expired_sets_error_status(
        self,
        mock_send,
        MockStagingService,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        from app.telegram.bot_api import TelegramFileExpiredError

        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        mock_get_bytes.side_effect = TelegramFileExpiredError("File expired")

        staging_svc = MockStagingService.return_value

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        staging_svc.set_error.assert_called_once()
        error_msg = staging_svc.set_error.call_args[0][1]
        assert "expired" in error_msg.lower() or "file" in error_msg.lower()

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks.StagingService")
    @patch("app.workers.ocr_tasks.send_message")
    def test_parse_error_sets_error_status(
        self,
        mock_send,
        MockStagingService,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        from app.llm.item_parser import ParseError

        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        mock_get_bytes.return_value = b"PDF bytes"
        mock_extract.side_effect = ParseError("No items extracted")

        staging_svc = MockStagingService.return_value

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        staging_svc.set_error.assert_called_once()

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks.StagingService")
    @patch("app.workers.ocr_tasks.send_message")
    def test_generic_exception_sets_error_status(
        self,
        mock_send,
        MockStagingService,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        mock_get_bytes.return_value = b"PDF bytes"
        mock_extract.side_effect = RuntimeError("unexpected crash")

        staging_svc = MockStagingService.return_value

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        staging_svc.set_error.assert_called_once()

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks.StagingService")
    @patch("app.workers.ocr_tasks.send_message")
    def test_empty_line_items_raises_parse_error(
        self,
        mock_send,
        MockStagingService,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        """Extracted data with no line_items should set error, not confirm."""
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging()
        db, fake_ctx = _patch_db_and_task(staging)

        mock_get_bytes.return_value = b"PDF bytes"
        mock_extract.return_value = {"supplier": "ACME", "line_items": []}  # empty

        staging_svc = MockStagingService.return_value

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "invoice")

        staging_svc.set_error.assert_called_once()


# ---------------------------------------------------------------------------
# Price list path
# ---------------------------------------------------------------------------


class TestPriceListTask:
    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_price_list_calls_parse_price_list(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging(document_type="price_list")
        db, fake_ctx = _patch_db_and_task(staging)

        supplier = _make_supplier("Fresh Farms")
        mock_resolve.return_value = (supplier, None)
        mock_extract.return_value = _PRICE_LIST_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 11

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            _run_task(STAGING_ID.hex, "price_list")

        # _extract should be called with document_type=price_list
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        doc_type_arg = call_args[0][2] if len(call_args[0]) >= 3 else call_args[1].get("document_type")
        assert doc_type_arg == "price_list"

    @patch("app.workers.ocr_tasks.get_settings")
    @patch("app.workers.ocr_tasks.worker_db_session")
    @patch("app.workers.ocr_tasks.get_file_bytes")
    @patch("app.workers.ocr_tasks._extract")
    @patch("app.workers.ocr_tasks._resolve_supplier")
    @patch("app.workers.ocr_tasks._send_with_keyboard")
    def test_price_list_review_shows_at_sign(
        self,
        mock_send,
        mock_resolve,
        mock_extract,
        mock_get_bytes,
        mock_db_ctx,
        mock_settings,
    ):
        """Price list review text should use '@' format, not '×'."""
        settings = _make_settings()
        mock_settings.return_value = settings

        staging = _make_staging(document_type="price_list")
        db, fake_ctx = _patch_db_and_task(staging)

        supplier = _make_supplier("Fresh Farms")
        mock_resolve.return_value = (supplier, None)
        mock_extract.return_value = _PRICE_LIST_EXTRACTED
        mock_get_bytes.return_value = b"PDF bytes"
        mock_send.return_value = 11

        captured_text = {}

        def capture_send(chat_id, text, reply_markup, settings):
            captured_text["text"] = text
            return 11

        with patch("app.workers.ocr_tasks.worker_db_session", fake_ctx):
            with patch("app.workers.ocr_tasks._send_with_keyboard", side_effect=capture_send):
                _run_task(STAGING_ID.hex, "price_list")

        assert "@" in captured_text.get("text", "")
        assert "×" not in captured_text.get("text", "")


# ---------------------------------------------------------------------------
# _resolve_supplier unit tests
# ---------------------------------------------------------------------------


class TestResolveSupplier:
    def test_no_name_returns_create_button(self):
        from app.workers.ocr_tasks import _resolve_supplier

        db = MagicMock()
        with patch("app.workers.ocr_tasks.SupplierService") as MockSvc:
            supplier, buttons = _resolve_supplier(
                name=None,
                restaurant_id=RESTAURANT_ID,
                staging_id=STAGING_ID,
                db=db,
            )

        assert supplier is None
        assert buttons is not None
        assert len(buttons) >= 1
        # Should have "Add Supplier" button somewhere
        all_texts = [b.get("text", "") for b in buttons]
        assert any("Add" in t or "Supplier" in t for t in all_texts)

    def test_high_score_returns_auto_match(self):
        from app.workers.ocr_tasks import _resolve_supplier

        db = MagicMock()
        matched_supplier = _make_supplier("ACME Foods")

        with patch("app.workers.ocr_tasks.SupplierService") as MockSvc:
            svc = MockSvc.return_value
            svc.fuzzy_search_for_restaurant.return_value = [(matched_supplier, 0.95)]

            supplier, buttons = _resolve_supplier(
                name="ACME Foods",
                restaurant_id=RESTAURANT_ID,
                staging_id=STAGING_ID,
                db=db,
            )

        assert supplier is matched_supplier
        assert buttons is None  # no manual selection needed

    def test_mid_score_returns_suggest_buttons(self):
        from app.workers.ocr_tasks import _resolve_supplier

        db = MagicMock()
        matched_supplier = _make_supplier("ACME Foods")

        with patch("app.workers.ocr_tasks.SupplierService") as MockSvc:
            svc = MockSvc.return_value
            svc.fuzzy_search_for_restaurant.return_value = [(matched_supplier, 0.65)]

            supplier, buttons = _resolve_supplier(
                name="ACME Foodz",
                restaurant_id=RESTAURANT_ID,
                staging_id=STAGING_ID,
                db=db,
            )

        assert supplier is None  # not auto-confirmed
        assert buttons is not None
        assert len(buttons) >= 2  # at least: use ACME + create new

    def test_low_score_returns_create_only(self):
        from app.workers.ocr_tasks import _resolve_supplier

        db = MagicMock()

        with patch("app.workers.ocr_tasks.SupplierService") as MockSvc:
            svc = MockSvc.return_value
            svc.fuzzy_search_for_restaurant.return_value = []  # no matches at threshold

            supplier, buttons = _resolve_supplier(
                name="Completely Unknown Supplier",
                restaurant_id=RESTAURANT_ID,
                staging_id=STAGING_ID,
                db=db,
            )

        assert supplier is None
        assert buttons is not None
        # Should have a "Create" / "➕" button
        all_texts = [b.get("text", "") for b in buttons]
        assert any("Create" in t or "➕" in t for t in all_texts)
