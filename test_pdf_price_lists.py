#!/usr/bin/env python3
"""
Test Google OCR models on supplier price list PDFs.

GOOGLE OCR ONLY - NO LLM VISION

This script tests Google Cloud OCR approaches:
1. Cloud Vision API - document_text_detection (per-page images)
2. Document AI - Form Parser
3. Document AI - OCR Processor (General)

For each approach, we measure:
- Accuracy (manual review)
- Speed (latency)
- Characters extracted
- Tables found (Document AI only)
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from google.api_core.client_options import ClientOptions
from google.cloud import documentai, vision
from google.oauth2 import service_account
from pdf2image import convert_from_path
from PIL import Image

# Add app to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import get_settings


# Test configuration
TEST_PDF_PATH = "test_data/PriceList.pdf"  # Cheong Hing Company meat price list
OUTPUT_DIR = "pdf_ocr_benchmark_results"
MAX_PAGES_TO_TEST = None  # Test ALL pages (None = no limit)

# Google Document AI processor types to test
# Will create new processors in US region
EXISTING_INVOICE_PROCESSOR_ID = None  # Set to None to create new Invoice Parser in US region

PROCESSOR_TYPES = [
    "OCR_PROCESSOR",              # General OCR
    "FORM_PARSER_PROCESSOR",      # Forms and tables
    "INVOICE_PROCESSOR",          # Invoice-specific (uses existing)
    "LAYOUT_PARSER_PROCESSOR",    # CRITICAL: Extracts tables, lists, structured layout
]


def get_google_credentials(settings):
    """Get Google Cloud credentials from settings."""
    if settings.google_cloud_credentials_path:
        return service_account.Credentials.from_service_account_file(
            settings.google_cloud_credentials_path
        )

    # Build from individual env vars
    credentials_info = {
        "type": "service_account",
        "project_id": settings.google_cloud_project_id,
        "private_key": settings.google_cloud_private_key.replace("\\n", "\n"),
        "client_email": settings.google_cloud_client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return service_account.Credentials.from_service_account_info(credentials_info)


def pdf_to_images(pdf_path: str, max_pages: int | None = None) -> list[bytes]:
    """Convert PDF pages to PNG images in memory."""
    print(f"Converting PDF to images (max {max_pages if max_pages else 'ALL'} pages)...")

    # Convert PDF to PIL Images
    if max_pages:
        images = convert_from_path(
            pdf_path,
            first_page=1,
            last_page=max_pages,
            dpi=200,  # Good balance of quality vs size
        )
    else:
        # Convert all pages
        images = convert_from_path(
            pdf_path,
            dpi=200,  # Good balance of quality vs size
        )

    # Convert to bytes
    image_bytes_list = []
    for idx, img in enumerate(images):
        # Resize if too large (max 4000px)
        max_side = 4000
        if max(img.width, img.height) > max_side:
            ratio = max_side / max(img.width, img.height)
            new_width = int(img.width * ratio)
            new_height = int(img.height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Convert to bytes
        import io

        output = io.BytesIO()
        img.save(output, format="PNG", quality=95)
        image_bytes_list.append(output.getvalue())
        print(
            f"  Page {idx + 1}: {img.width}x{img.height} -> {len(output.getvalue()) / 1024:.1f}KB"
        )

    return image_bytes_list


def test_google_vision_api(image_bytes_list: list[bytes], settings, method: str = "document") -> dict:
    """Test Google Cloud Vision API text detection.

    Args:
        method: Either "document" for DOCUMENT_TEXT_DETECTION or "text" for TEXT_DETECTION
    """
    method_name = "document_text_detection" if method == "document" else "text_detection"
    display_name = "DOCUMENT_TEXT_DETECTION" if method == "document" else "TEXT_DETECTION"

    print("\n" + "=" * 80)
    print(f"Testing: Google Cloud Vision API ({display_name})")
    print("=" * 80)

    credentials = get_google_credentials(settings)
    # Pylance can't always resolve dynamically generated GAPIC methods.
    client = cast(Any, vision.ImageAnnotatorClient(credentials=credentials))

    start_time = time.time()
    all_pages_text = []

    for page_num, image_bytes in enumerate(image_bytes_list, 1):
        print(f"Processing page {page_num}...")
        image = vision.Image(content=image_bytes)

        # Call the appropriate method
        if method == "document":
            response = client.document_text_detection(image=image)
        else:
            response = client.text_detection(image=image)

        if response.error.message:
            raise Exception(f"Vision API error: {response.error.message}")

        # Extract full text
        if response.full_text_annotation:
            page_text = response.full_text_annotation.text
            all_pages_text.append(f"# Page {page_num}\n\n{page_text}")
        else:
            all_pages_text.append(f"# Page {page_num}\n\n(No text detected)")

    latency = time.time() - start_time
    output_text = "\n\n".join(all_pages_text)

    return {
        "method": f"Google Vision API ({display_name})",
        "latency_seconds": round(latency, 2),
        "output_text": output_text,
        "chars_extracted": len(output_text),
        "pages_processed": len(image_bytes_list),
    }


def test_document_ai_processor(
    pdf_bytes: bytes, processor_type: str, settings
) -> dict:
    """Test Google Document AI with a specific processor type."""
    print("\n" + "=" * 80)
    print(f"Testing: Google Document AI ({processor_type})")
    print("=" * 80)

    credentials = get_google_credentials(settings)
    location = settings.google_cloud_location
    project_id = settings.google_cloud_project_id

    # Setup client with regional endpoint
    if location and location != "us":
        api_endpoint = f"{location}-documentai.googleapis.com"
        client_options = ClientOptions(api_endpoint=api_endpoint)
        client = documentai.DocumentProcessorServiceClient(
            credentials=credentials, client_options=client_options
        )
    else:
        client = documentai.DocumentProcessorServiceClient(credentials=credentials)

    # Use existing processor for Invoice Parser, or create/find others
    parent = f"projects/{project_id}/locations/{location}"
    processor_name = None

    if processor_type == "INVOICE_PROCESSOR" and EXISTING_INVOICE_PROCESSOR_ID:
        # Use the existing Invoice Parser
        processor_name = f"projects/{project_id}/locations/{location}/processors/{EXISTING_INVOICE_PROCESSOR_ID}"
        print(f"Using existing Invoice Parser: {processor_name}")
    else:
        # List existing processors
        print(f"Looking for existing {processor_type} processor...")
        list_request = documentai.ListProcessorsRequest(parent=parent)
        processors = client.list_processors(request=list_request)

        for processor in processors:
            if processor.type_ == processor_type:
                processor_name = processor.name
                print(f"Found existing processor: {processor_name}")
                break

        if not processor_name:
            print(f"No existing {processor_type} processor found. Creating one...")
            # Note: This requires proper IAM permissions
            create_request = documentai.CreateProcessorRequest(
                parent=parent,
                processor=documentai.Processor(
                    display_name=f"Test {processor_type}",
                    type_=processor_type,
                ),
            )
            try:
                processor = client.create_processor(request=create_request)
                processor_name = processor.name
                print(f"Created processor: {processor_name}")
            except Exception as e:
                print(f"Failed to create processor: {e}")
                return {
                    "method": f"Document AI ({processor_type})",
                    "error": str(e),
                    "latency_seconds": 0,
                    "output_text": "",
                }

    # Process document
    print("Processing document...")
    start_time = time.time()

    raw_document = documentai.RawDocument(
        content=pdf_bytes,
        mime_type="application/pdf",
    )

    request = documentai.ProcessRequest(
        name=processor_name,
        raw_document=raw_document,
    )

    result = client.process_document(request=request)
    document = result.document

    latency = time.time() - start_time

    # Extract text and structure
    output_lines = []
    output_lines.append(f"# Document Text ({processor_type})\n")
    output_lines.append(document.text)
    output_lines.append("\n\n# Extracted Tables\n")

    # Extract tables if available
    for page_idx, page in enumerate(document.pages, 1):
        if page.tables:
            output_lines.append(f"\n## Page {page_idx} Tables\n")
            for table_idx, table in enumerate(page.tables, 1):
                output_lines.append(f"\n### Table {table_idx}\n")

                # Build markdown table
                rows = []
                for row in table.body_rows:
                    cells = []
                    for cell in row.cells:
                        cell_text = extract_text_from_layout(cell.layout, document.text)
                        cells.append(cell_text.strip().replace("\n", " "))
                    rows.append(cells)

                # Print as markdown table
                if rows:
                    header = rows[0] if rows else []
                    output_lines.append("| " + " | ".join(header) + " |")
                    output_lines.append("|" + "|".join(["---"] * len(header)) + "|")
                    for row in rows[1:]:
                        output_lines.append("| " + " | ".join(row) + " |")

    output_text = "\n".join(output_lines)

    return {
        "method": f"Document AI ({processor_type})",
        "latency_seconds": round(latency, 2),
        "output_text": output_text,
        "chars_extracted": len(output_text),
        "tables_found": sum(len(page.tables) for page in document.pages),
    }


def extract_text_from_layout(layout, full_text: str) -> str:
    """Extract text from a layout element using text anchors."""
    text_segments = []
    for segment in layout.text_anchor.text_segments:
        start = int(segment.start_index) if segment.start_index else 0
        end = int(segment.end_index) if segment.end_index else len(full_text)
        text_segments.append(full_text[start:end])
    return "".join(text_segments)


def main():
    """Run all OCR tests on the sample PDF."""
    settings = get_settings()

    # Check if test PDF exists
    if not os.path.exists(TEST_PDF_PATH):
        print(f"Error: Test PDF not found at {TEST_PDF_PATH}")
        print("Please place a sample price list PDF at this path.")
        return

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Testing PDF: {TEST_PDF_PATH}")
    print(f"Max pages: {MAX_PAGES_TO_TEST}")
    print(f"Output directory: {OUTPUT_DIR}")

    # Convert PDF to images for Vision API
    image_bytes_list = pdf_to_images(TEST_PDF_PATH, max_pages=MAX_PAGES_TO_TEST)

    # Read PDF as bytes for Document AI
    with open(TEST_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    # Run tests
    results = []

    # Test 1: Google Vision API - DOCUMENT_TEXT_DETECTION
    try:
        result = test_google_vision_api(image_bytes_list, settings, method="document")
        results.append(result)

        # Save output
        output_file = f"{OUTPUT_DIR}/01_Vision_DOCUMENT_TEXT_{timestamp}.md"
        with open(output_file, "w") as f:
            f.write(result["output_text"])
        print(f"Saved to: {output_file}")
    except Exception as e:
        print(f"Vision API (DOCUMENT_TEXT_DETECTION) test failed: {e}")
        results.append(
            {
                "method": "Google Vision API (DOCUMENT_TEXT_DETECTION)",
                "error": str(e),
                "latency_seconds": 0,
            }
        )

    # Test 2: Google Vision API - TEXT_DETECTION
    try:
        result = test_google_vision_api(image_bytes_list, settings, method="text")
        results.append(result)

        # Save output
        output_file = f"{OUTPUT_DIR}/02_Vision_TEXT_{timestamp}.md"
        with open(output_file, "w") as f:
            f.write(result["output_text"])
        print(f"Saved to: {output_file}")
    except Exception as e:
        print(f"Vision API (TEXT_DETECTION) test failed: {e}")
        results.append(
            {
                "method": "Google Vision API (TEXT_DETECTION)",
                "error": str(e),
                "latency_seconds": 0,
            }
        )

    # Test 3-6: Document AI processors
    for idx, processor_type in enumerate(PROCESSOR_TYPES, 3):
        try:
            result = test_document_ai_processor(
                pdf_bytes, processor_type, settings
            )
            results.append(result)

            if result.get("output_text"):
                # Save output
                processor_name = (
                    processor_type.replace("_PROCESSOR", "").title().replace("_", " ")
                )
                output_file = f"{OUTPUT_DIR}/{idx:02d}_Document_AI_{processor_name}_{timestamp}.md"
                with open(output_file, "w") as f:
                    f.write(result["output_text"])
                print(f"Saved to: {output_file}")
        except Exception as e:
            print(f"Document AI ({processor_type}) test failed: {e}")
            results.append(
                {
                    "method": f"Document AI ({processor_type})",
                    "error": str(e),
                    "latency_seconds": 0,
                }
            )

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Method':<40} {'Speed':<12} {'Chars':<12} {'Status'}")
    print("-" * 80)

    for result in results:
        method = result["method"]
        speed = f"{result.get('latency_seconds', 0):.2f}s"
        chars = str(result.get("chars_extracted", 0))
        status = "ERROR" if "error" in result else "SUCCESS"
        print(f"{method:<40} {speed:<12} {chars:<12} {status}")

    # Save summary JSON
    summary_file = f"{OUTPUT_DIR}/summary_{timestamp}.json"
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSummary saved to: {summary_file}")


if __name__ == "__main__":
    main()
