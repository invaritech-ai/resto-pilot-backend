"""Unit tests for app/services/staging_service.py.

Uses a mocked SQLAlchemy session — no real DB required.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call

import pytest

from app.services.staging_service import StagingNotFoundError, StagingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STAGING_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
RESTAURANT_ID = uuid.uuid4()


def _make_session() -> MagicMock:
    return MagicMock()


def _make_staging(
    status: str = "processing",
    document_type: str | None = None,
    staging_id: uuid.UUID | None = None,
) -> MagicMock:
    s = MagicMock()
    s.id = staging_id or STAGING_ID
    s.status = status
    s.document_type = document_type
    s.extracted_data_json = None
    s.error_message = None
    s.supplier_id = None
    s.updated_at = None
    return s


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_staging_record(self):
        session = _make_session()
        svc = StagingService(session)

        result = svc.create(
            uploaded_by=USER_ID,
            restaurant_id=RESTAURANT_ID,
            file_id="file123",
            file_unique_id="uniq456",
            mime="application/pdf",
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.uploaded_by == USER_ID
        assert added.restaurant_id == RESTAURANT_ID
        assert added.file_id == "file123"
        assert added.file_unique_id == "uniq456"
        assert added.mime == "application/pdf"
        assert added.status == "processing"

    def test_session_id_is_optional(self):
        session = _make_session()
        svc = StagingService(session)

        svc.create(
            uploaded_by=USER_ID,
            restaurant_id=RESTAURANT_ID,
            file_id="f1",
            file_unique_id="u1",
        )

        added = session.add.call_args[0][0]
        assert added.session_id is None

    def test_session_id_stored_when_provided(self):
        session = _make_session()
        svc = StagingService(session)
        session_id = uuid.uuid4()

        svc.create(
            uploaded_by=USER_ID,
            restaurant_id=RESTAURANT_ID,
            file_id="f2",
            file_unique_id="u2",
            session_id=session_id,
        )

        added = session.add.call_args[0][0]
        assert added.session_id == session_id

    def test_returns_staging_object(self):
        session = _make_session()
        svc = StagingService(session)

        result = svc.create(
            uploaded_by=USER_ID,
            restaurant_id=RESTAURANT_ID,
            file_id="f3",
            file_unique_id="u3",
        )

        # Returns whatever was added (the ORM model)
        assert result is session.add.call_args[0][0]

    def test_does_not_commit(self):
        session = _make_session()
        svc = StagingService(session)

        svc.create(
            uploaded_by=USER_ID,
            restaurant_id=RESTAURANT_ID,
            file_id="f4",
            file_unique_id="u4",
        )

        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    def test_returns_none_when_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        result = svc.get(STAGING_ID)

        assert result is None
        session.get.assert_called_once()

    def test_returns_staging_when_found(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        result = svc.get(STAGING_ID)

        assert result is staging


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------


class TestRequire:
    def test_raises_when_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        with pytest.raises(StagingNotFoundError):
            svc.require(STAGING_ID)

    def test_returns_staging_when_found(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        result = svc.require(STAGING_ID)

        assert result is staging


# ---------------------------------------------------------------------------
# set_document_type()
# ---------------------------------------------------------------------------


class TestSetDocumentType:
    def test_sets_invoice_type(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        result = svc.set_document_type(STAGING_ID, "invoice")

        assert staging.document_type == "invoice"
        session.flush.assert_called_once()
        assert result is staging

    def test_sets_price_list_type(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_document_type(STAGING_ID, "price_list")

        assert staging.document_type == "price_list"

    def test_raises_for_invalid_type(self):
        session = _make_session()
        svc = StagingService(session)

        with pytest.raises(ValueError, match="invoice.*price_list"):
            svc.set_document_type(STAGING_ID, "receipt")

    def test_raises_when_staging_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        with pytest.raises(StagingNotFoundError):
            svc.set_document_type(STAGING_ID, "invoice")

    def test_does_not_commit(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_document_type(STAGING_ID, "invoice")

        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# set_extracted_data()
# ---------------------------------------------------------------------------


class TestSetExtractedData:
    def test_sets_json_and_status(self):
        session = _make_session()
        staging = _make_staging(status="processing")
        session.get.return_value = staging
        svc = StagingService(session)

        data = {"supplier": "ACME", "line_items": [{"name": "Tomato", "unit_price": 5.0}]}
        result = svc.set_extracted_data(STAGING_ID, data)

        assert staging.extracted_data_json == data
        assert staging.status == "pending_review"
        session.flush.assert_called_once()
        assert result is staging

    def test_atomically_sets_both_fields(self):
        """Both extracted_data_json and status must change in same flush call."""
        session = _make_session()
        staging = _make_staging(status="processing")
        session.get.return_value = staging

        captured = {}

        def _flush():
            captured["json"] = staging.extracted_data_json
            captured["status"] = staging.status

        session.flush.side_effect = _flush
        StagingService(session).set_extracted_data(STAGING_ID, {"line_items": []})

        assert captured["json"] is not None
        assert captured["status"] == "pending_review"

    def test_raises_when_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        with pytest.raises(StagingNotFoundError):
            svc.set_extracted_data(STAGING_ID, {})

    def test_updates_updated_at(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_extracted_data(STAGING_ID, {"line_items": []})

        assert staging.updated_at is not None


# ---------------------------------------------------------------------------
# set_error()
# ---------------------------------------------------------------------------


class TestSetError:
    def test_sets_error_status_and_message(self):
        session = _make_session()
        staging = _make_staging(status="processing")
        session.get.return_value = staging
        svc = StagingService(session)

        result = svc.set_error(STAGING_ID, "OCR failed")

        assert staging.status == "error"
        assert staging.error_message == "OCR failed"
        assert result is staging

    def test_returns_none_when_staging_missing(self):
        """set_error should NOT raise even if record doesn't exist."""
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        result = svc.set_error(STAGING_ID, "some error")

        assert result is None

    def test_flushes_on_success(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_error(STAGING_ID, "LLM parse error")

        session.flush.assert_called_once()

    def test_does_not_flush_when_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        svc.set_error(STAGING_ID, "error msg")

        session.flush.assert_not_called()


# ---------------------------------------------------------------------------
# set_supplier()
# ---------------------------------------------------------------------------


class TestSetSupplier:
    def test_sets_supplier_id(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        supplier_id = uuid.uuid4()
        result = svc.set_supplier(STAGING_ID, supplier_id)

        assert staging.supplier_id == supplier_id
        session.flush.assert_called_once()
        assert result is staging

    def test_raises_when_not_found(self):
        session = _make_session()
        session.get.return_value = None
        svc = StagingService(session)

        with pytest.raises(StagingNotFoundError):
            svc.set_supplier(STAGING_ID, uuid.uuid4())

    def test_updates_updated_at(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_supplier(STAGING_ID, uuid.uuid4())

        assert staging.updated_at is not None

    def test_does_not_commit(self):
        session = _make_session()
        staging = _make_staging()
        session.get.return_value = staging
        svc = StagingService(session)

        svc.set_supplier(STAGING_ID, uuid.uuid4())

        session.commit.assert_not_called()
