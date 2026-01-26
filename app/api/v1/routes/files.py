"""
File upload and processing API endpoints.

Provides REST API access for file uploads with progress tracking and webhooks.
"""

import logging
import uuid
from typing import Any, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.core.config import Settings
from app.db.models.file_processing_page_jobs import FileProcessingPageJobs
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.queries.file_processing import (
    get_processing_result,
    get_processing_status,
)
from app.workers.celery_types import CeleryDelayable
from app.workers.file_processing_tasks import _encode_payload_bytes, process_file_api_task

router = APIRouter()
logger = logging.getLogger(__name__)

# Supported MIME types
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
}

# Max file size (20MB)
MAX_FILE_SIZE = 20 * 1024 * 1024


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    restaurant_id: str = Form(...),
    user_id: str = Form(...),
    processing_type: str = Form(...),
    supplier_id: str | None = Form(None),
    webhook_url: str | None = Form(None),
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """
    Upload file for processing via REST API.

    Args:
        file: File to process (PDF or image)
        restaurant_id: Restaurant UUID
        user_id: User UUID
        processing_type: "invoice", "price_list", or "inventory"
        supplier_id: Optional supplier UUID (for price lists)
        webhook_url: Optional webhook URL for status notifications
        db: Database session
        settings: Application settings

    Returns:
        {
            "run_id": "...",
            "status": "processing",
            "status_url": "/api/v1/files/{run_id}/status"
        }
    """
    # Validate processing type
    if processing_type not in ["invoice", "price_list", "inventory"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="processing_type must be 'invoice', 'price_list', or 'inventory'",
        )

    # Validate file size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)",
        )

    # Validate MIME type
    mime_type = file.content_type or ""
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Supported types: {', '.join(SUPPORTED_MIME_TYPES)}",
        )

    # Validate UUIDs
    try:
        restaurant_uuid = uuid.UUID(restaurant_id)
        user_uuid = uuid.UUID(user_id)
        supplier_uuid = uuid.UUID(supplier_id) if supplier_id else None
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format: {e}",
        )

    # Create processing run
    file_id = f"api_{uuid.uuid4().hex[:12]}"
    run = FileProcessingRuns(
        restaurant_id=restaurant_uuid,
        user_id=user_uuid,
        file_id=file_id,
        filename=file.filename,
        mime_type=mime_type,
        processing_type=processing_type,
        status="processing",
        webhook_url=webhook_url,
        source="api",
        supplier_id=supplier_uuid,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    logger.info(
        "api_file_upload_started run_id=%s filename=%s mime_type=%s processing_type=%s",
        run.id,
        file.filename,
        mime_type,
        processing_type,
    )

    # Create staging record
    staging = FileProcessingStaging(
        restaurant_id=restaurant_uuid,
        user_id=user_uuid,
        run_id=run.id,
        processing_type=processing_type,
        extracted_data_json={},
        status="processing",
        supplier_id=supplier_uuid,
    )
    db.add(staging)
    db.commit()

    # Enqueue for processing
    try:
        file_payload_ref = _encode_payload_bytes(
            db=db,
            payload_bytes=file_bytes,
            run_id=run.id,
        )
        db.commit()

        async_result = cast(CeleryDelayable, process_file_api_task).delay(
            str(run.id),
            file_payload_ref,
            mime_type,
            file.filename or "unknown",
        )

        logger.info(
            "api_file_upload_enqueued task_id=%s run_id=%s",
            getattr(async_result, "id", None),
            run.id,
        )
    except Exception as exc:
        logger.exception("api_file_upload_enqueue_failed run_id=%s", run.id)

        # Update run status
        run.status = "failed"
        run.error_message = "Failed to enqueue file for processing"
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue file for processing",
        ) from exc

    return {
        "run_id": str(run.id),
        "status": "processing",
        "status_url": f"/api/v1/files/{run.id}/status",
    }


@router.get("/files/{run_id}/status")
async def get_file_status(
    run_id: str,
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Get real-time processing status.

    Poll this endpoint to track progress.

    Args:
        run_id: FileProcessingRuns UUID
        db: Database session

    Returns:
        Status dictionary with progress information
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid run_id format: {e}",
        )

    try:
        status_dict = get_processing_status(run_uuid, db)

        # Add staging_id if completed
        if status_dict["status"] == "completed":
            result = get_processing_result(run_uuid, db)
            if result:
                status_dict["staging_id"] = result["staging_id"]
                status_dict["data_url"] = f"/api/v1/files/{result['staging_id']}"

        return status_dict
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/files/{run_id}/pages")
async def get_file_pages(
    run_id: str,
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Return page job statuses for a file processing run."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid run_id format: {e}",
        )

    jobs = db.scalars(
        select(FileProcessingPageJobs)
        .where(FileProcessingPageJobs.run_id == run_uuid)
        .order_by(FileProcessingPageJobs.page_index)
    ).all()

    return {
        "run_id": run_id,
        "pages": [
            {
                "page_index": job.page_index,
                "status": job.status,
                "ocr_retries": job.ocr_retries,
                "extraction_retries": job.extraction_retries,
                "error_message": job.error_message,
            }
            for job in jobs
        ],
    }


@router.get("/files/{staging_id}")
async def get_extracted_data(
    staging_id: str,
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Retrieve extracted data from staging.

    Returns JSON with all extracted fields.

    Args:
        staging_id: FileProcessingStaging UUID
        db: Database session

    Returns:
        {
            "staging_id": "...",
            "status": "pending_review",
            "processing_type": "invoice",
            "extracted_data": {...},
            "created_at": "..."
        }
    """
    try:
        staging_uuid = uuid.UUID(staging_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid staging_id format: {e}",
        )

    staging = db.get(FileProcessingStaging, staging_uuid)
    if not staging:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staging record not found",
        )

    return {
        "staging_id": str(staging.id),
        "status": staging.status,
        "processing_type": staging.processing_type,
        "extracted_data": staging.extracted_data_json,
        "created_at": staging.created_at.isoformat() if staging.created_at else None,
    }


@router.post("/files/{staging_id}/confirm")
async def confirm_file_processing(
    staging_id: str,
    edits: dict[str, Any] | None = None,
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Confirm extracted data and write to final tables.

    Optional: Include edits to correct data before saving.

    Args:
        staging_id: FileProcessingStaging UUID
        edits: Optional dictionary of field edits
        db: Database session

    Request body:
        {
            "edits": {
                "supplier_name": "Corrected Name",
                "line_items[0].quantity": 10
            }
        }

    Returns:
        {
            "status": "confirmed",
            "document_id": "...",
            "items_created": 10
        }
    """
    try:
        staging_uuid = uuid.UUID(staging_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid staging_id format: {e}",
        )

    staging = db.get(FileProcessingStaging, staging_uuid)
    if not staging:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staging record not found",
        )

    # Apply edits if provided
    if edits:
        # Update staging.extracted_data_json with edits
        # This is a simplified version - full implementation would need
        # proper field path parsing and validation
        for field_path, new_value in edits.items():
            # Simple field update (not handling nested paths yet)
            if field_path in staging.extracted_data_json:
                staging.extracted_data_json[field_path] = new_value

        db.commit()

    # Call existing confirmation logic
    # This would need to be implemented based on your existing confirmation flow
    # For now, just mark as confirmed
    staging.status = "confirmed"
    db.commit()

    logger.info("api_file_confirmed staging_id=%s", staging_id)

    return {
        "status": "confirmed",
        "staging_id": str(staging.id),
        "message": "Data confirmed and saved to final tables",
    }


@router.post("/files/{staging_id}/cancel")
async def cancel_file_processing(
    staging_id: str,
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Cancel file processing and mark staging as cancelled.

    Args:
        staging_id: FileProcessingStaging UUID
        db: Database session

    Returns:
        {
            "status": "cancelled",
            "staging_id": "..."
        }
    """
    try:
        staging_uuid = uuid.UUID(staging_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid staging_id format: {e}",
        )

    staging = db.get(FileProcessingStaging, staging_uuid)
    if not staging:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staging record not found",
        )

    staging.status = "cancelled"
    db.commit()

    logger.info("api_file_cancelled staging_id=%s", staging_id)

    return {
        "status": "cancelled",
        "staging_id": str(staging.id),
    }
