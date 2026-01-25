#!/usr/bin/env python3
"""
Test script for page-by-page PDF processing with snapshot recovery.

Usage:
    python test_page_processor.py path/to/invoice.pdf
    python test_page_processor.py path/to/invoice.pdf --resume  # Resume from last snapshot

This script:
1. Creates test database records (FileProcessingRuns, FileProcessingStaging)
2. Processes the PDF page by page with snapshots
3. Shows progress with detailed logging
4. Demonstrates snapshot-based recovery
"""

import sys
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.session import get_db
from app.processing.page_processor import process_file_with_snapshots


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_page_processor.py <pdf_file_path> [--resume]")
        print("\nExample:")
        print("  python test_page_processor.py invoice.pdf")
        print("  python test_page_processor.py invoice.pdf --resume  # Resume from snapshots")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    resume_mode = "--resume" in sys.argv

    if not pdf_path.exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    print("=" * 80)
    print("PAGE-BY-PAGE PDF PROCESSOR TEST")
    print("=" * 80)
    print(f"File: {pdf_path}")
    print(f"Size: {pdf_path.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"Resume mode: {'ON' if resume_mode else 'OFF'}")
    print("=" * 80)

    # Read file
    print("\n[1/6] Reading file...")
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()
    print(f"✓ Read {len(file_bytes):,} bytes")

    # Get settings and DB
    print("\n[2/6] Initializing database connection...")
    settings = get_settings()
    db_gen = get_db()
    db = next(db_gen)
    print("✓ Connected to database")

    # Get or create test user and restaurant
    print("\n[3/6] Setting up test data...")
    from sqlalchemy import select
    from app.db.models.restaurant import Restaurant
    from app.db.models.user import User

    test_user = db.scalar(select(User).limit(1))
    test_restaurant = db.scalar(select(Restaurant).limit(1))

    if not test_user or not test_restaurant:
        print("✗ Error: No user or restaurant found in database")
        print("  Please create a user and restaurant first")
        sys.exit(1)

    print(f"✓ Using user: {test_user.id}")
    print(f"✓ Using restaurant: {test_restaurant.id}")

    # Create or reuse FileProcessingRuns record
    if resume_mode:
        print("\n[4/6] Looking for existing processing run...")
        from app.db.models.file_processing_steps import FileProcessingSteps

        # Find most recent run for this file
        run = db.scalar(
            select(FileProcessingRuns)
            .where(
                FileProcessingRuns.filename == pdf_path.name,
                FileProcessingRuns.status.in_(["processing", "failed"])
            )
            .order_by(FileProcessingRuns.created_at.desc())
        )

        if run:
            print(f"✓ Found existing run: {run.id}")
            print(f"  Status: {run.status}")
            print(f"  Pages processed: {run.pages_processed}/{run.pages_total}")

            # Show completed pages
            completed_steps = db.scalars(
                select(FileProcessingSteps)
                .where(
                    FileProcessingSteps.run_id == run.id,
                    FileProcessingSteps.status == "completed"
                )
                .order_by(FileProcessingSteps.page_index)
            ).all()

            if completed_steps:
                stages = {}
                for step in completed_steps:
                    stages.setdefault(step.stage, []).append(step.page_index)

                print(f"  Completed stages:")
                for stage, pages in stages.items():
                    print(f"    - {stage}: pages {sorted(pages)}")
        else:
            print("✗ No existing run found, creating new one...")
            resume_mode = False
            run = None
    else:
        run = None

    if not run:
        print("\n[4/6] Creating FileProcessingRuns record...")
        run = FileProcessingRuns(
            restaurant_id=test_restaurant.id,
            user_id=test_user.id,
            file_id=f"test_{uuid.uuid4().hex[:12]}",
            filename=pdf_path.name,
            mime_type="application/pdf",
            processing_type="invoice",
            status="processing",
        )
        db.add(run)
        db.flush()
        print(f"✓ Created run: {run.id}")

        # Create staging record
        staging = FileProcessingStaging(
            restaurant_id=test_restaurant.id,
            user_id=test_user.id,
            run_id=run.id,
            processing_type="invoice",
            extracted_data_json={},
            status="processing",
        )
        db.add(staging)
        db.commit()
        print(f"✓ Created staging: {staging.id}")

    # Process file with snapshots
    print("\n[5/6] Processing PDF page by page...")
    print("=" * 80)
    print("Watch for:")
    print("  - OCR → Markdown conversion for each page")
    print("  - Markdown → JSON extraction for each page")
    print("  - Snapshot saves after each page")
    print("  - Progress updates")
    print("=" * 80)
    print()

    try:
        result = process_file_with_snapshots(
            run_id=run.id,
            file_bytes=file_bytes,
            mime_type="application/pdf",
            filename=pdf_path.name,
            processing_type="invoice",
            db=db,
            settings=settings,
        )

        print("\n" + "=" * 80)
        print("[6/6] PROCESSING COMPLETE!")
        print("=" * 80)

        # Reload run to get updated stats
        db.refresh(run)

        print(f"Status: {run.status}")
        print(f"Pages processed: {run.pages_processed}/{run.pages_total}")
        print(f"Time elapsed: {(run.finished_at - run.started_at).total_seconds():.1f}s")

        # Show telemetry
        telemetry = result.get("telemetry", [])
        if telemetry:
            total_latency = sum(t.latency_ms for t in telemetry if t.latency_ms)
            print(f"Total API latency: {total_latency / 1000:.1f}s")
            print(f"API calls: {len(telemetry)}")

        # Show extracted data summary
        extracted_data = result.get("data", {})
        print(f"\nExtracted data keys: {list(extracted_data.keys())}")

        if "supplier_name" in extracted_data:
            print(f"Supplier: {extracted_data.get('supplier_name', 'N/A')}")
        if "invoice_number" in extracted_data:
            print(f"Invoice #: {extracted_data.get('invoice_number', 'N/A')}")
        if "line_items" in extracted_data:
            print(f"Line items: {len(extracted_data.get('line_items', []))}")

        print("\n✓ SUCCESS! Check the database:")
        print(f"  - file_processing_runs table: run_id = {run.id}")
        print(f"  - file_processing_steps table: all page snapshots")
        print(f"  - file_processing_staging table: extracted data")

    except KeyboardInterrupt:
        print("\n\n⚠️  INTERRUPTED! Processing was stopped.")
        print("=" * 80)
        print("To resume from where you left off, run:")
        print(f"  python test_page_processor.py {pdf_path} --resume")
        print("=" * 80)
        db.close()
        sys.exit(1)

    except Exception as exc:
        print(f"\n✗ ERROR: {exc}")
        import traceback
        traceback.print_exc()

        # Show what was completed before failure
        db.refresh(run)
        print(f"\nPartially completed:")
        print(f"  Pages processed: {run.pages_processed}/{run.pages_total}")
        print(f"  Status: {run.status}")
        print(f"  Error: {run.error_message}")

        print("\nTo resume, run:")
        print(f"  python test_page_processor.py {pdf_path} --resume")
        db.close()
        sys.exit(1)

    db.close()
    print("\n✓ Test complete!")


if __name__ == "__main__":
    main()
