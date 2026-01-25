"""
Page-by-page file processing engine with snapshot-based recovery.

Processes files (PDFs, images, spreadsheets) page by page with two-stage approach:
1. OCR → Markdown for each page
2. Markdown → JSON extraction for each page

Saves snapshots after each page to enable recovery from failures.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.vision_client import (
    VisionCallResult,
    _merge_structured_results,
    ocr_page_to_markdown,
    extract_json_from_markdown,
)
from app.core.config import Settings
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_steps import FileProcessingSteps
from app.processing.file_processor import _format_schema_for_extraction
from app.processing.webhooks import send_webhook_notification

logger = logging.getLogger(__name__)


def process_file_with_snapshots(
    *,
    run_id: UUID,
    file_bytes: bytes,
    mime_type: str,
    filename: str | None,
    processing_type: str,
    db: Session,
    settings: Settings,
) -> dict[str, Any]:
    """
    Process file with page-by-page snapshots for recovery.

    Args:
        run_id: FileProcessingRuns ID for tracking
        file_bytes: File content in memory
        mime_type: MIME type of file
        filename: Original filename
        processing_type: "invoice", "price_list", or "inventory"
        db: Database session
        settings: Application settings

    Returns:
        {
            "data": extracted_data_json,
            "telemetry": [VisionCallResult, ...]
        }
    """
    run = db.get(FileProcessingRuns, run_id)
    if not run:
        raise ValueError(f"FileProcessingRun not found: {run_id}")

    # Update run status
    run.started_at = dt.datetime.now(dt.UTC)
    run.current_stage = "initializing"
    db.commit()

    try:
        # Route to appropriate handler based on MIME type
        if mime_type == "application/pdf":
            result = process_pdf_page_by_page(
                run_id=run_id,
                file_bytes=file_bytes,
                filename=filename,
                processing_type=processing_type,
                db=db,
                settings=settings,
            )
        elif mime_type.startswith("image/"):
            result = process_image_file(
                run_id=run_id,
                file_bytes=file_bytes,
                mime_type=mime_type,
                processing_type=processing_type,
                db=db,
                settings=settings,
            )
        else:
            raise ValueError(f"Unsupported MIME type: {mime_type}")

        # Update run to completed
        run.status = "completed"
        run.current_stage = "finalize"
        run.finished_at = dt.datetime.now(dt.UTC)
        db.commit()

        # Send completion webhook if configured
        if run.webhook_url:
            # Get staging_id from result if available
            staging_id = result.get("staging_id")  # Will be added by caller
            send_webhook_notification(
                webhook_url=run.webhook_url,
                run_id=run.id,
                status="completed",
                progress_percentage=100.0,
                pages_processed=run.pages_processed or 0,
                pages_total=run.pages_total,
                staging_id=staging_id,
            )

        return result

    except Exception as exc:
        # Update run to failed
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = dt.datetime.now(dt.UTC)
        db.commit()

        # Send failure webhook if configured
        if run.webhook_url:
            send_webhook_notification(
                webhook_url=run.webhook_url,
                run_id=run.id,
                status="failed",
                progress_percentage=0.0,
                pages_processed=run.pages_processed or 0,
                pages_total=run.pages_total,
                error_message=str(exc),
            )

        raise


def process_pdf_page_by_page(
    *,
    run_id: UUID,
    file_bytes: bytes,
    filename: str | None,
    processing_type: str,
    db: Session,
    settings: Settings,
) -> dict[str, Any]:
    """
    Process PDF file page by page with snapshots.

    Each page goes through two stages:
    1. OCR → Markdown (save snapshot)
    2. Markdown → JSON (save snapshot)

    Then all JSON results are merged.

    Args:
        run_id: FileProcessingRuns ID
        file_bytes: PDF file bytes
        filename: Original filename
        processing_type: "invoice", "price_list", or "inventory"
        db: Database session
        settings: Application settings

    Returns:
        {"data": merged_json, "telemetry": [VisionCallResult, ...]}
    """
    from app.ai.vision_client import _convert_pdf_pages_to_images

    run = db.get(FileProcessingRuns, run_id)

    # Convert PDF to images
    logger.info(f"Converting PDF to images: {filename}")
    run.current_stage = "pdf_to_images"
    db.commit()

    image_pages = _convert_pdf_pages_to_images(file_bytes)
    total_pages = len(image_pages)

    run.pages_total = total_pages
    run.pages_processed = 0
    db.commit()

    logger.info(f"PDF has {total_pages} pages")

    # Check for existing completed pages (recovery)
    completed_ocr_pages = load_completed_pages(run_id, "ocr", db)
    completed_extraction_pages = load_completed_pages(run_id, "extraction", db)

    telemetry_results: list[VisionCallResult] = []

    # Stage 1: OCR each page to Markdown
    logger.info(f"Starting OCR stage for {total_pages} pages")
    run.current_stage = "ocr"
    db.commit()

    for page_num, page_image_bytes in image_pages:
        # Skip if already completed (recovery)
        if page_num in completed_ocr_pages:
            logger.info(f"Page {page_num}/{total_pages}: OCR already completed (recovery)")
            continue

        logger.info(f"Page {page_num}/{total_pages}: Starting OCR")
        run.current_stage = f"ocr_page_{page_num}"
        db.commit()

        # Perform OCR
        ocr_result = ocr_page_to_markdown(
            page_image_bytes=page_image_bytes,
            page_num=page_num,
            total_pages=total_pages,
            settings=settings,
        )
        telemetry_results.append(ocr_result)

        # Save snapshot
        save_page_snapshot(
            run_id=run_id,
            page_index=page_num,
            stage="ocr",
            output_text=ocr_result.content or "",
            status="completed",
            db=db,
        )

        # Update progress
        run.pages_processed = page_num
        db.commit()

        # Send progress webhook every 10 pages
        if run.webhook_url and page_num % 10 == 0:
            progress_pct = (page_num / total_pages) * 100 if total_pages > 0 else 0
            send_webhook_notification(
                webhook_url=run.webhook_url,
                run_id=run.id,
                status="processing",
                progress_percentage=progress_pct,
                pages_processed=page_num,
                pages_total=total_pages,
            )

        logger.info(f"Page {page_num}/{total_pages}: OCR completed ({ocr_result.latency_ms}ms)")

    # Stage 2: Extract JSON from Markdown for each page
    logger.info(f"Starting extraction stage for {total_pages} pages")
    run.current_stage = "extraction"
    db.commit()

    # Get database schema for extraction
    schema_tables_map = {
        "invoice": ["invoices", "invoice_line_items"],
        "price_list": ["suppliers", "supplier_items", "supplier_prices"],
        "inventory": ["inventory_batches"],
    }
    schema_tables = schema_tables_map.get(processing_type, [])
    db_schema = _format_schema_for_extraction(schema_tables) if schema_tables else ""

    for page_num in range(1, total_pages + 1):
        # Skip if already completed (recovery)
        if page_num in completed_extraction_pages:
            logger.info(f"Page {page_num}/{total_pages}: Extraction already completed (recovery)")
            continue

        logger.info(f"Page {page_num}/{total_pages}: Starting extraction")
        run.current_stage = f"extraction_page_{page_num}"
        db.commit()

        # Load markdown from previous stage
        markdown_text = completed_ocr_pages.get(page_num, "")
        if not markdown_text:
            # If not in cache, load from database
            ocr_step = db.scalar(
                select(FileProcessingSteps).where(
                    FileProcessingSteps.run_id == run_id,
                    FileProcessingSteps.stage == "ocr",
                    FileProcessingSteps.page_index == page_num,
                )
            )
            markdown_text = ocr_step.output_text if ocr_step else ""

        if not markdown_text:
            logger.warning(f"Page {page_num}/{total_pages}: No OCR markdown found, skipping extraction")
            continue

        # Perform extraction
        extraction_result = extract_json_from_markdown(
            markdown_text=markdown_text,
            page_num=page_num,
            processing_type=processing_type,
            db_schema=db_schema,
            settings=settings,
        )
        telemetry_results.append(extraction_result)

        # Save snapshot
        save_page_snapshot(
            run_id=run_id,
            page_index=page_num,
            stage="extraction",
            output_text=extraction_result.content or "{}",
            status="completed",
            db=db,
        )

        logger.info(f"Page {page_num}/{total_pages}: Extraction completed ({extraction_result.latency_ms}ms)")

    # Stage 3: Merge all extraction results
    logger.info("Starting merge stage")
    run.current_stage = "merge"
    db.commit()

    merged_data = merge_page_results(run_id, db)

    logger.info("Merge completed")
    return {"data": merged_data, "telemetry": telemetry_results}


def process_image_file(
    *,
    run_id: UUID,
    file_bytes: bytes,
    mime_type: str,
    processing_type: str,
    db: Session,
    settings: Settings,
) -> dict[str, Any]:
    """
    Process a single image file (simpler than PDF - just one page).

    Args:
        run_id: FileProcessingRuns ID
        file_bytes: Image file bytes
        mime_type: Image MIME type
        processing_type: "invoice", "price_list", or "inventory"
        db: Database session
        settings: Application settings

    Returns:
        {"data": extracted_json, "telemetry": [VisionCallResult, ...]}
    """
    run = db.get(FileProcessingRuns, run_id)

    run.pages_total = 1
    run.pages_processed = 0
    run.current_stage = "ocr"
    db.commit()

    telemetry_results: list[VisionCallResult] = []

    # Stage 1: OCR
    logger.info("Single image: Starting OCR")
    ocr_result = ocr_page_to_markdown(
        page_image_bytes=file_bytes,
        page_num=1,
        total_pages=1,
        settings=settings,
    )
    telemetry_results.append(ocr_result)

    save_page_snapshot(
        run_id=run_id,
        page_index=1,
        stage="ocr",
        output_text=ocr_result.content or "",
        status="completed",
        db=db,
    )

    # Stage 2: Extraction
    logger.info("Single image: Starting extraction")
    run.current_stage = "extraction"
    db.commit()

    schema_tables_map = {
        "invoice": ["invoices", "invoice_line_items"],
        "price_list": ["suppliers", "supplier_items", "supplier_prices"],
        "inventory": ["inventory_batches"],
    }
    schema_tables = schema_tables_map.get(processing_type, [])
    db_schema = _format_schema_for_extraction(schema_tables) if schema_tables else ""

    extraction_result = extract_json_from_markdown(
        markdown_text=ocr_result.content or "",
        page_num=1,
        processing_type=processing_type,
        db_schema=db_schema,
        settings=settings,
    )
    telemetry_results.append(extraction_result)

    save_page_snapshot(
        run_id=run_id,
        page_index=1,
        stage="extraction",
        output_text=extraction_result.content or "{}",
        status="completed",
        db=db,
    )

    run.pages_processed = 1
    db.commit()

    # Parse JSON directly (no merge needed for single page)
    try:
        extracted_data = json.loads(extraction_result.content or "{}")
    except json.JSONDecodeError:
        logger.exception("Failed to parse extraction JSON")
        extracted_data = {}

    return {"data": extracted_data, "telemetry": telemetry_results}


def save_page_snapshot(
    *,
    run_id: UUID,
    page_index: int,
    stage: str,
    output_text: str,
    status: str,
    db: Session,
) -> FileProcessingSteps:
    """
    Save a snapshot of page processing progress.

    Creates or updates a FileProcessingSteps record for this page/stage.

    Args:
        run_id: FileProcessingRuns ID
        page_index: Page number (1-indexed)
        stage: "ocr" or "extraction"
        output_text: OCR markdown or extracted JSON
        status: "started", "completed", or "failed"
        db: Database session

    Returns:
        FileProcessingSteps record
    """
    # Check if step already exists
    existing_step = db.scalar(
        select(FileProcessingSteps).where(
            FileProcessingSteps.run_id == run_id,
            FileProcessingSteps.stage == stage,
            FileProcessingSteps.page_index == page_index,
        )
    )

    if existing_step:
        # Update existing
        existing_step.output_text = output_text
        existing_step.status = status
        step = existing_step
    else:
        # Create new
        step = FileProcessingSteps(
            run_id=run_id,
            stage=stage,
            page_index=page_index,
            output_text=output_text,
            status=status,
        )
        db.add(step)

    db.commit()
    return step


def load_completed_pages(
    run_id: UUID,
    stage: str,
    db: Session,
) -> dict[int, str]:
    """
    Load completed pages for a stage (for recovery).

    Args:
        run_id: FileProcessingRuns ID
        stage: "ocr" or "extraction"
        db: Database session

    Returns:
        Dict mapping page_index → output_text
    """
    steps = db.scalars(
        select(FileProcessingSteps).where(
            FileProcessingSteps.run_id == run_id,
            FileProcessingSteps.stage == stage,
            FileProcessingSteps.status == "completed",
        )
    ).all()

    return {step.page_index: step.output_text or "" for step in steps if step.page_index is not None}


def merge_page_results(run_id: UUID, db: Session) -> dict[str, Any]:
    """
    Merge extraction results from all pages.

    Reads all "extraction" stage steps and merges their JSON using
    the existing _merge_structured_results() logic.

    Args:
        run_id: FileProcessingRuns ID
        db: Database session

    Returns:
        Merged JSON dictionary
    """
    # Load all extraction results
    extraction_steps = db.scalars(
        select(FileProcessingSteps)
        .where(
            FileProcessingSteps.run_id == run_id,
            FileProcessingSteps.stage == "extraction",
            FileProcessingSteps.status == "completed",
        )
        .order_by(FileProcessingSteps.page_index)
    ).all()

    if not extraction_steps:
        logger.warning("No extraction results found to merge")
        return {}

    # Parse JSON from each page
    page_results: list[dict[str, Any]] = []
    for step in extraction_steps:
        try:
            # Clean JSON string (remove markdown code fences and special tokens)
            raw_json = step.output_text or "{}"

            # Remove markdown code blocks
            if raw_json.strip().startswith("```"):
                # Find the JSON content between ```json and ```
                lines = raw_json.strip().split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]  # Remove opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove closing fence
                raw_json = "\n".join(lines)

            # Remove special tokens (like <|begin_of_box|> and <|end_of_box|>)
            raw_json = raw_json.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")

            page_data = json.loads(raw_json.strip())
            page_results.append(page_data)
        except json.JSONDecodeError:
            logger.exception(f"Failed to parse JSON for page {step.page_index}")
            continue

    if not page_results:
        logger.warning("No valid JSON results to merge")
        return {}

    # Use existing merge logic from vision_client
    merged = _merge_structured_results(page_results)
    return merged
