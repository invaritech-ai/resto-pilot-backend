"""Tests for supplier text-input flow in app/telegram/handlers/buttons.py."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.telegram.handlers.buttons import handle, handle_supplier_name_input
from app.telegram.keyboards import uuid_to_hex


def _make_user(chat_id: int = 123) -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.chat_id = chat_id
    user.context = {}
    return user


def _make_settings() -> MagicMock:
    return MagicMock()


def _make_staging(staging_id: uuid.UUID) -> MagicMock:
    staging = MagicMock()
    staging.id = staging_id
    staging.restaurant_id = uuid.uuid4()
    staging.status = "pending_review"
    staging.document_type = "invoice"
    staging.extracted_data_json = {"line_items": [{"name": "Tomato", "qty": 2.0}]}
    staging.supplier_id = None
    return staging


def _make_callback_update(callback_data: str, message_id: int = 88) -> dict:
    return {
        "callback_query": {
            "id": "cb-1",
            "data": callback_data,
            "message": {"message_id": message_id, "chat": {"id": 123}},
        }
    }


def _make_text_update(text: str) -> dict:
    return {"message": {"text": text}}


class TestSupplierInputFlow:
    def test_new_sup_without_name_prompts_for_typed_supplier(self):
        staging_id = uuid.uuid4()
        staging = _make_staging(staging_id)
        staging.extracted_data_json = {"line_items": [{"name": "Tomato", "qty": 2.0}]}

        user = _make_user()
        staging.uploaded_by = user.id
        db = MagicMock()
        db.get.return_value = staging
        ctx_svc = MagicMock()

        with (
            patch("app.telegram.handlers.buttons.send_message") as mock_send,
            patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq,
        ):
            handle(
                _make_callback_update(f"new_sup:{uuid_to_hex(staging_id)}"),
                user,
                db,
                ctx_svc,
                _make_settings(),
            )

        ctx_svc.set_fields.assert_called_once_with(
            user,
            supplier_input_staging_id=str(staging_id),
            supplier_input_mode="create",
        )
        assert db.commit.call_count >= 1
        assert "Type the supplier name" in mock_send.call_args.kwargs["text"]
        assert "Enter supplier name" in mock_acq.call_args.kwargs["text"]

    def test_typed_supplier_resolve_mode_autosets_supplier(self):
        staging_id = uuid.uuid4()
        user = _make_user()
        staging = _make_staging(staging_id)
        staging.uploaded_by = user.id
        db = MagicMock()
        db.get.return_value = staging

        ctx_svc = MagicMock()
        ctx_svc.get_fields.return_value = {
            "supplier_input_staging_id": str(staging_id),
            "supplier_input_mode": "resolve",
            "review_message_id": 55,
        }

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.name = "Fresh Co"

        with (
            patch("app.workers.ocr_tasks._resolve_supplier", return_value=(supplier, None)),
            patch("app.telegram.handlers.buttons._edit_supplier_in_review") as mock_edit,
            patch("app.telegram.handlers.buttons.send_message") as mock_send,
        ):
            handle_supplier_name_input(
                _make_text_update("Fresh Co"),
                user,
                db,
                ctx_svc,
                _make_settings(),
            )

        assert staging.extracted_data_json["supplier"] == "Fresh Co"
        assert staging.supplier_id == supplier.id
        mock_edit.assert_called_once()
        assert "Supplier set" in mock_send.call_args.kwargs["text"]
        ctx_svc.set_fields.assert_called_once_with(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )

    def test_typed_supplier_create_mode_creates_and_links(self):
        staging_id = uuid.uuid4()
        user = _make_user()
        staging = _make_staging(staging_id)
        staging.uploaded_by = user.id
        db = MagicMock()
        db.get.return_value = staging

        ctx_svc = MagicMock()
        ctx_svc.get_fields.return_value = {
            "supplier_input_staging_id": str(staging_id),
            "supplier_input_mode": "create",
            "review_message_id": 77,
        }

        created = MagicMock()
        created.id = uuid.uuid4()
        created.name = "Brand New Supply"

        with (
            patch("app.telegram.handlers.buttons.SupplierService") as MockSvc,
            patch("app.telegram.handlers.buttons._edit_supplier_in_review") as mock_edit,
            patch("app.telegram.handlers.buttons.send_message") as mock_send,
        ):
            MockSvc.return_value.create.return_value = created
            handle_supplier_name_input(
                _make_text_update("Brand New Supply"),
                user,
                db,
                ctx_svc,
                _make_settings(),
            )

        MockSvc.return_value.create.assert_called_once_with(
            name="Brand New Supply",
            user_id=user.id,
            restaurant_id=staging.restaurant_id,
        )
        assert staging.supplier_id == created.id
        mock_edit.assert_called_once()
        assert "Supplier created" in mock_send.call_args.kwargs["text"]
        ctx_svc.set_fields.assert_called_once_with(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
