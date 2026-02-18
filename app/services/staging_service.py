"""
Staging service — CRUD for FileProcessingStaging.

Responsibilities:
    create()              — new staging record (status=processing)
    get()                 — load by id (returns None if not found)
    require()             — load by id (raises StagingNotFoundError if not found)
    set_document_type()   — update document_type
    set_extracted_data()  — write extracted_data_json + set status=pending_review
    set_status()          — update status only
    set_error()           — set error_message + status=error

Invariants:
    - Callers own the commit. This service only flushes.
    - set_extracted_data() is atomic: json + status both change in one flush.
    - set_error() never raises even if the staging record is missing (logs a warning).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy.orm import Session

from app.db.models.file_processing_staging import FileProcessingStaging

logger = logging.getLogger(__name__)


class StagingNotFoundError(Exception):
    pass


class StagingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        uploaded_by: uuid.UUID,
        restaurant_id: uuid.UUID,
        file_id: str,
        file_unique_id: str,
        mime: str | None = None,
        session_id: uuid.UUID | None = None,
    ) -> FileProcessingStaging:
        """Create a new staging record in 'processing' status.

        Caller must commit after this returns.
        """
        staging = FileProcessingStaging(
            uploaded_by=uploaded_by,
            restaurant_id=restaurant_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            mime=mime,
            session_id=session_id,
            status="processing",
        )
        self.session.add(staging)
        self.session.flush()
        return staging

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, staging_id: uuid.UUID) -> FileProcessingStaging | None:
        """Return staging record or None if not found."""
        return self.session.get(FileProcessingStaging, staging_id)

    def require(self, staging_id: uuid.UUID) -> FileProcessingStaging:
        """Return staging record.

        Raises:
            StagingNotFoundError: if staging_id does not exist.
        """
        staging = self.session.get(FileProcessingStaging, staging_id)
        if staging is None:
            raise StagingNotFoundError(f"FileProcessingStaging {staging_id} not found")
        return staging

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def set_document_type(
        self, staging_id: uuid.UUID, document_type: str
    ) -> FileProcessingStaging:
        """Update document_type on staging record.

        Args:
            document_type: 'invoice' or 'price_list'

        Raises:
            StagingNotFoundError: record not found.
            ValueError: document_type not in allowed values.
        """
        if document_type not in ("invoice", "price_list"):
            raise ValueError(
                f"document_type must be 'invoice' or 'price_list', got {document_type!r}"
            )
        staging = self.require(staging_id)
        staging.document_type = document_type
        self.session.flush()
        return staging

    def set_extracted_data(
        self, staging_id: uuid.UUID, data: dict
    ) -> FileProcessingStaging:
        """Write extracted JSON data and transition status to pending_review.

        Atomically: sets extracted_data_json + status=pending_review in one flush.

        Raises:
            StagingNotFoundError: record not found.
        """
        staging = self.require(staging_id)
        staging.extracted_data_json = data
        staging.status = "pending_review"
        staging.updated_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        return staging

    def set_status(self, staging_id: uuid.UUID, status: str) -> FileProcessingStaging:
        """Update status only.

        Args:
            status: one of processing / pending_review / confirmed / cancelled / error

        Raises:
            StagingNotFoundError: record not found.
        """
        staging = self.require(staging_id)
        staging.status = status
        staging.updated_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        return staging

    def set_error(self, staging_id: uuid.UUID, message: str) -> FileProcessingStaging:
        """Set status=error and record the error message.

        Silently logs and returns None if the record no longer exists
        (e.g. race condition with a delete) rather than raising.
        """
        staging = self.session.get(FileProcessingStaging, staging_id)
        if staging is None:
            logger.warning(
                "staging_set_error: record not found staging_id=%s message=%s",
                staging_id,
                message,
            )
            return None  # type: ignore[return-value]
        staging.status = "error"
        staging.error_message = message
        staging.updated_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        return staging

    def set_supplier(
        self, staging_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> FileProcessingStaging:
        """Set the resolved supplier_id on a staging record.

        Raises:
            StagingNotFoundError: record not found.
        """
        staging = self.require(staging_id)
        staging.supplier_id = supplier_id
        staging.updated_at = dt.datetime.now(tz=dt.timezone.utc)
        self.session.flush()
        return staging
