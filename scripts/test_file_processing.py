#!/usr/bin/env python3
"""
Test script for file processing capabilities.
Uses the exact internal functions that Celery tasks use.

Run from project root: uv run python scripts/test_file_processing.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

print("[INIT] Starting test script...")
print(f"[INIT] Python: {sys.executable}")
print(f"[INIT] Working dir: {Path.cwd()}")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
print(f"[INIT] Added to path: {Path(__file__).parent.parent}")

print("[INIT] Importing modules...")
try:
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy import create_engine
    print("[INIT] ✓ SQLAlchemy imported")
except ImportError as e:
    print(f"[INIT] ✗ SQLAlchemy import failed: {e}")
    sys.exit(1)

try:
    from app.core.config import get_settings
    print("[INIT] ✓ Config imported")
except ImportError as e:
    print(f"[INIT] ✗ Config import failed: {e}")
    sys.exit(1)

try:
    from app.processing.file_processor import (
        extract_price_list_data,
        extract_invoice_data,
        extract_inventory_data,
    )
    print("[INIT] ✓ File processor imported")
except ImportError as e:
    print(f"[INIT] ✗ File processor import failed: {e}")
    traceback.print_exc()
    sys.exit(1)

print("[INIT] All imports successful!")


def get_db_session() -> Session:
    """Create a database session for testing."""
    print("[DB] Creating database session...")
    settings = get_settings()
    print(f"[DB] Database URL: {settings.database_url[:50]}...")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    print("[DB] Engine created")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    print("[DB] ✓ Session created")
    return session


def test_price_list_extraction(pdf_path: str) -> None:
    """Test price list extraction using exact internal functions."""
    print(f"\n{'='*60}")
    print(f"Testing Price List Extraction")
    print(f"File: {pdf_path}")
    print(f"{'='*60}\n")

    # Load file
    print("[FILE] Loading file...")
    file_bytes = Path(pdf_path).read_bytes()
    print(f"[FILE] ✓ Loaded: {len(file_bytes):,} bytes")

    # Determine mime type
    filename = Path(pdf_path).name
    if filename.lower().endswith('.pdf'):
        mime_type = "application/pdf"
    elif filename.lower().endswith(('.jpg', '.jpeg')):
        mime_type = "image/jpeg"
    elif filename.lower().endswith('.png'):
        mime_type = "image/png"
    elif filename.lower().endswith('.csv'):
        mime_type = "text/csv"
    elif filename.lower().endswith('.xlsx'):
        mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        mime_type = "application/octet-stream"
    
    print(f"[FILE] MIME type: {mime_type}")
    print(f"[FILE] Filename: {filename}")

    # Get settings
    print("\n[CONFIG] Loading settings...")
    settings = get_settings()
    print(f"[CONFIG] vision_model: {settings.vision_model or '(not set)'}")
    print(f"[CONFIG] vision_base_url: {settings.vision_base_url or '(not set)'}")
    print(f"[CONFIG] vision_api_key: {'***' + settings.vision_api_key[-4:] if settings.vision_api_key else 'NOT SET'}")
    print(f"[CONFIG] openai_model: {settings.openai_model}")
    print(f"[CONFIG] openai_base_url: {settings.openai_base_url}")

    # Create DB session
    print("\n[DB] Connecting to database...")
    try:
        db = get_db_session()
    except Exception as e:
        print(f"[DB] ✗ Connection failed: {e}")
        traceback.print_exc()
        return

    try:
        print(f"\n[EXTRACT] Calling extract_price_list_data()...")
        print(f"[EXTRACT] This is the exact function used by process_price_list_file_task")
        print(f"[EXTRACT] Starting extraction (this may take a while for large PDFs)...")
        
        extracted_data = extract_price_list_data(
            file_bytes=file_bytes,
            mime_type=mime_type,
            settings=settings,
            db=db,
            filename=filename,
        )

        print(f"\n[EXTRACT] ✓ Extraction complete!")
        
        # Check telemetry
        telemetry_results = extracted_data.pop("_telemetry_results", [])
        print(f"\n[TELEMETRY] Total LLM calls: {len(telemetry_results)}")
        for i, t in enumerate(telemetry_results):
            status = "✓" if not t.error else "✗"
            print(f"[TELEMETRY] {status} Call {i+1}:")
            print(f"[TELEMETRY]     model: {t.model}")
            print(f"[TELEMETRY]     latency: {t.latency_ms}ms")
            print(f"[TELEMETRY]     generation_id: {t.openrouter_generation_id}")
            if t.usage:
                print(f"[TELEMETRY]     usage: {t.usage}")
            if t.error:
                print(f"[TELEMETRY]     ERROR: {t.error}")

        # Show extracted data
        print(f"\n[RESULT] --- Extracted Data ---")
        print(f"[RESULT] supplier_name: {extracted_data.get('supplier_name')}")
        print(f"[RESULT] currency: {extracted_data.get('currency')}")
        print(f"[RESULT] effective_date: {extracted_data.get('effective_date')}")
        
        items = extracted_data.get('items', [])
        print(f"[RESULT] items count: {len(items)}")
        
        if items:
            print(f"\n[RESULT] --- First 5 Items ---")
            for i, item in enumerate(items[:5]):
                print(f"\n[RESULT] Item {i+1}:")
                print(f"[RESULT]   supplier_name_raw: {item.get('supplier_name_raw')}")
                print(f"[RESULT]   price: {item.get('price')}")
                print(f"[RESULT]   currency: {item.get('currency')}")
                print(f"[RESULT]   pack_size_text: {item.get('pack_size_text')}")
                print(f"[RESULT]   unit_basis: {item.get('unit_basis')}")
                print(f"[RESULT]   source_page: {item.get('source_page')}")

        # Full JSON output
        print(f"\n[RESULT] --- Full JSON Output (first 5000 chars) ---")
        json_str = json.dumps(extracted_data, indent=2, ensure_ascii=False, default=str)
        print(json_str[:5000])
        if len(json_str) > 5000:
            print("[RESULT] ... (truncated)")

    except Exception as e:
        print(f"\n[EXTRACT] ✗ Extraction FAILED: {e}")
        traceback.print_exc()
    finally:
        db.close()
        print(f"\n[DB] ✓ Database session closed")


def test_invoice_extraction(file_path: str) -> None:
    """Test invoice extraction using exact internal functions."""
    print(f"\n{'='*60}")
    print(f"Testing Invoice Extraction")
    print(f"File: {file_path}")
    print(f"{'='*60}\n")

    print("[FILE] Loading file...")
    file_bytes = Path(file_path).read_bytes()
    print(f"[FILE] ✓ Loaded: {len(file_bytes):,} bytes")
    
    filename = Path(file_path).name
    if filename.lower().endswith('.pdf'):
        mime_type = "application/pdf"
    elif filename.lower().endswith(('.jpg', '.jpeg')):
        mime_type = "image/jpeg"
    else:
        mime_type = "application/octet-stream"
    print(f"[FILE] MIME type: {mime_type}")

    print("\n[CONFIG] Loading settings...")
    settings = get_settings()
    print(f"[CONFIG] vision_model: {settings.vision_model or '(not set)'}")

    print("\n[DB] Connecting to database...")
    db = get_db_session()

    try:
        print(f"\n[EXTRACT] Calling extract_invoice_data()...")
        extracted_data = extract_invoice_data(
            file_bytes=file_bytes,
            mime_type=mime_type,
            settings=settings,
            db=db,
            filename=filename,
        )

        telemetry_results = extracted_data.pop("_telemetry_results", [])
        print(f"[EXTRACT] ✓ Extraction complete!")
        print(f"[TELEMETRY] Calls: {len(telemetry_results)}")
        
        print(f"\n[RESULT] --- Extracted Data ---")
        print(json.dumps(extracted_data, indent=2, ensure_ascii=False, default=str)[:3000])

    except Exception as e:
        print(f"[EXTRACT] ✗ Extraction FAILED: {e}")
        traceback.print_exc()
    finally:
        db.close()
        print(f"\n[DB] ✓ Database session closed")


if __name__ == "__main__":
    print("\n[MAIN] Script started")
    
    # Default test file
    default_pdf = "/Users/avi/Documents/Projects/AI/resto-pilot/docs/sample_files/Wholesales Catalog with Price List 2025.8 V6 name card (Teresa).pdf"

    file_path = sys.argv[1] if len(sys.argv) > 1 else default_pdf
    print(f"[MAIN] File path: {file_path}")
    
    # Determine extraction type from filename or arg
    extraction_type = "price_list"  # default
    if len(sys.argv) > 2:
        extraction_type = sys.argv[2]
    elif "invoice" in file_path.lower():
        extraction_type = "invoice"
    elif "inventory" in file_path.lower():
        extraction_type = "inventory"

    print(f"[MAIN] Extraction type: {extraction_type}")

    if not Path(file_path).exists():
        print(f"[MAIN] ✗ Error: File not found: {file_path}")
        sys.exit(1)
    print(f"[MAIN] ✓ File exists")

    if extraction_type == "price_list":
        test_price_list_extraction(file_path)
    elif extraction_type == "invoice":
        test_invoice_extraction(file_path)
    else:
        print(f"[MAIN] ✗ Unknown extraction type: {extraction_type}")
        sys.exit(1)

    print("\n[MAIN] Script finished")
