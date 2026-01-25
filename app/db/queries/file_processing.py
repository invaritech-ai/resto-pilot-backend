"""
Query functions for file processing status and history.

Provides real-time status tracking for file processing jobs.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_steps import FileProcessingSteps
from app.db.models.file_processing_staging import FileProcessingStaging


def get_processing_status(run_id: UUID, db: Session) -> dict:
    """
    Get real-time status of file processing.

    Args:
        run_id: FileProcessingRuns ID
        db: Database session

    Returns:
        {
            "run_id": "...",
            "status": "processing",
            "current_stage": "ocr_page_45",
            "pages_total": 100,
            "pages_processed": 45,
            "progress_percentage": 45.0,
            "started_at": "2024-01-15T10:30:00Z",
            "elapsed_seconds": 120,
            "estimated_seconds_remaining": 150,
            "error_message": null
        }
    """
    run = db.get(FileProcessingRuns, run_id)
    if not run:
        raise ValueError(f"FileProcessingRun not found: {run_id}")

    # Calculate progress percentage
    progress_pct = 0.0
    if run.pages_total and run.pages_total > 0:
        progress_pct = (run.pages_processed / run.pages_total) * 100

    # Calculate elapsed time
    elapsed_seconds = 0
    if run.started_at:
        end_time = run.finished_at if run.finished_at else dt.datetime.now(dt.UTC)
        elapsed_seconds = int((end_time - run.started_at).total_seconds())

    # Estimate remaining time
    estimated_seconds_remaining = None
    if run.status == "processing" and run.pages_processed and run.pages_total:
        if run.pages_processed > 0 and elapsed_seconds > 0:
            seconds_per_page = elapsed_seconds / run.pages_processed
            remaining_pages = run.pages_total - run.pages_processed
            estimated_seconds_remaining = int(seconds_per_page * remaining_pages)

    return {
        "run_id": str(run.id),
        "status": run.status,
        "current_stage": run.current_stage or "initializing",
        "pages_total": run.pages_total,
        "pages_processed": run.pages_processed or 0,
        "progress_percentage": round(progress_pct, 1),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "elapsed_seconds": elapsed_seconds,
        "estimated_seconds_remaining": estimated_seconds_remaining,
        "error_message": run.error_message,
    }


def get_processing_history(run_id: UUID, db: Session) -> dict:
    """
    Get detailed processing history including all page snapshots.

    Useful for debugging and understanding what happened during processing.

    Args:
        run_id: FileProcessingRuns ID
        db: Database session

    Returns:
        {
            "run_id": "...",
            "status": "completed",
            "pages_total": 6,
            "steps": [
                {
                    "page_index": 1,
                    "stage": "ocr",
                    "status": "completed",
                    "output_length": 1234,
                    "created_at": "..."
                },
                ...
            ]
        }
    """
    run = db.get(FileProcessingRuns, run_id)
    if not run:
        raise ValueError(f"FileProcessingRun not found: {run_id}")

    # Get all processing steps
    steps = db.scalars(
        select(FileProcessingSteps)
        .where(FileProcessingSteps.run_id == run_id)
        .order_by(FileProcessingSteps.page_index, FileProcessingSteps.stage)
    ).all()

    step_dicts = [
        {
            "page_index": step.page_index,
            "stage": step.stage,
            "status": step.status,
            "output_length": len(step.output_text) if step.output_text else 0,
            "created_at": step.created_at.isoformat() if step.created_at else None,
        }
        for step in steps
    ]

    return {
        "run_id": str(run.id),
        "status": run.status,
        "current_stage": run.current_stage,
        "pages_total": run.pages_total,
        "pages_processed": run.pages_processed,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_message": run.error_message,
        "steps": step_dicts,
    }


def get_user_processing_jobs(
    user_id: UUID, db: Session, limit: int = 10
) -> list[dict]:
    """
    Get recent file processing jobs for a user.

    Useful for "what's the status of my uploads?" queries.

    Args:
        user_id: User ID
        db: Database session
        limit: Maximum number of jobs to return (default 10)

    Returns:
        List of job status dictionaries (same format as get_processing_status)
    """
    # Get recent runs for this user
    runs = db.scalars(
        select(FileProcessingRuns)
        .where(FileProcessingRuns.user_id == user_id)
        .order_by(FileProcessingRuns.created_at.desc())
        .limit(limit)
    ).all()

    return [get_processing_status(run.id, db) for run in runs]


def get_processing_result(run_id: UUID, db: Session) -> dict | None:
    """
    Get extracted data from completed processing job.

    Args:
        run_id: FileProcessingRuns ID
        db: Database session

    Returns:
        {
            "staging_id": "...",
            "status": "pending_review",
            "processing_type": "invoice",
            "extracted_data": {...}
        }
        Returns None if no staging record found.
    """
    staging = db.scalar(
        select(FileProcessingStaging).where(FileProcessingStaging.run_id == run_id)
    )

    if not staging:
        return None

    return {
        "staging_id": str(staging.id),
        "status": staging.status,
        "processing_type": staging.processing_type,
        "extracted_data": staging.extracted_data_json,
        "created_at": staging.created_at.isoformat() if staging.created_at else None,
    }
