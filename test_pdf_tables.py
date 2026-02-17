#!/usr/bin/env python3
"""
Test PDF table extraction libraries on supplier price list PDFs.

NO OCR - Direct PDF table parsing for digitally-generated PDFs.

This script tests:
1. pdfplumber - Excellent general-purpose table detection
2. camelot - Specialized for complex tables
3. tabula - Good for structured tables

For each library, we measure:
- Accuracy (tables found, data completeness)
- Speed (latency)
- Table structure preservation
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# Test configuration
TEST_PDF_PATH = "test_data/Wholesales Catalog with Price List 2025.8 V6 name card (Teresa).pdf"
OUTPUT_DIR = "pdf_table_extraction_results"
MAX_PAGES_TO_TEST = None  # Test ALL pages (None = no limit)


def test_pdfplumber(pdf_path: str) -> dict:
    """Test pdfplumber for table extraction."""
    print("\n" + "=" * 80)
    print("Testing: pdfplumber")
    print("=" * 80)

    try:
        import pdfplumber

        start_time = time.time()
        all_tables = []
        page_count = 0

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                if MAX_PAGES_TO_TEST and page_num > MAX_PAGES_TO_TEST:
                    break

                page_count = page_num
                print(f"Processing page {page_num}...")

                # Extract tables from page
                tables = page.extract_tables()

                if tables:
                    for table_idx, table in enumerate(tables, 1):
                        if table:  # Skip empty tables
                            # Convert to DataFrame for better structure
                            df = pd.DataFrame(table[1:], columns=table[0] if table else None)
                            all_tables.append({
                                "page": page_num,
                                "table_index": table_idx,
                                "rows": len(df),
                                "columns": len(df.columns),
                                "data": df,
                            })
                            print(f"  Found table {table_idx}: {len(df)} rows × {len(df.columns)} columns")

        latency = time.time() - start_time

        # Format output as markdown
        output_lines = ["# pdfplumber Table Extraction\n"]
        total_rows = 0

        for table_info in all_tables:
            output_lines.append(f"\n## Page {table_info['page']} - Table {table_info['table_index']}\n")
            output_lines.append(f"**Size:** {table_info['rows']} rows × {table_info['columns']} columns\n")
            output_lines.append("\n" + table_info['data'].to_markdown(index=False) + "\n")
            total_rows += table_info['rows']

        output_text = "\n".join(output_lines)

        return {
            "method": "pdfplumber",
            "success": True,
            "latency_seconds": round(latency, 2),
            "pages_processed": page_count,
            "tables_found": len(all_tables),
            "total_rows": total_rows,
            "output_text": output_text,
            "tables_data": all_tables,
        }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "method": "pdfplumber",
            "success": False,
            "error": str(e),
            "latency_seconds": 0,
        }


def test_camelot(pdf_path: str) -> dict:
    """Test camelot for table extraction."""
    print("\n" + "=" * 80)
    print("Testing: camelot")
    print("=" * 80)

    try:
        import camelot

        start_time = time.time()

        # Try both lattice (for tables with visible borders) and stream (for borderless)
        print("Trying lattice mode (tables with borders)...")
        tables = camelot.read_pdf(
            pdf_path,
            pages="all" if not MAX_PAGES_TO_TEST else f"1-{MAX_PAGES_TO_TEST}",
            flavor="lattice",
        )

        if len(tables) == 0:
            print("No tables found with lattice, trying stream mode (borderless tables)...")
            tables = camelot.read_pdf(
                pdf_path,
                pages="all" if not MAX_PAGES_TO_TEST else f"1-{MAX_PAGES_TO_TEST}",
                flavor="stream",
            )

        latency = time.time() - start_time

        print(f"Found {len(tables)} tables")

        # Format output as markdown
        output_lines = ["# camelot Table Extraction\n"]
        all_tables = []
        total_rows = 0

        for idx, table in enumerate(tables, 1):
            df = table.df
            page_num = table.page

            output_lines.append(f"\n## Page {page_num} - Table {idx}\n")
            output_lines.append(f"**Size:** {len(df)} rows × {len(df.columns)} columns\n")
            output_lines.append(f"**Accuracy:** {table.accuracy:.1f}%\n")
            output_lines.append("\n" + df.to_markdown(index=False) + "\n")

            all_tables.append({
                "page": page_num,
                "table_index": idx,
                "rows": len(df),
                "columns": len(df.columns),
                "accuracy": table.accuracy,
                "data": df,
            })
            total_rows += len(df)

            print(f"  Table {idx} (page {page_num}): {len(df)} rows × {len(df.columns)} columns (accuracy: {table.accuracy:.1f}%)")

        output_text = "\n".join(output_lines)

        return {
            "method": "camelot",
            "success": True,
            "latency_seconds": round(latency, 2),
            "tables_found": len(tables),
            "total_rows": total_rows,
            "output_text": output_text,
            "tables_data": all_tables,
        }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "method": "camelot",
            "success": False,
            "error": str(e),
            "latency_seconds": 0,
        }


def test_tabula(pdf_path: str) -> dict:
    """Test tabula for table extraction."""
    print("\n" + "=" * 80)
    print("Testing: tabula-py")
    print("=" * 80)

    try:
        import tabula

        start_time = time.time()

        # Read tables from all pages
        pages = "all" if not MAX_PAGES_TO_TEST else list(range(1, MAX_PAGES_TO_TEST + 1))

        dfs = tabula.read_pdf(
            pdf_path,
            pages=pages,
            multiple_tables=True,
            pandas_options={"header": None},
        )

        latency = time.time() - start_time

        print(f"Found {len(dfs)} tables")

        # Format output as markdown
        output_lines = ["# tabula-py Table Extraction\n"]
        all_tables = []
        total_rows = 0

        for idx, df in enumerate(dfs, 1):
            if df.empty:
                continue

            output_lines.append(f"\n## Table {idx}\n")
            output_lines.append(f"**Size:** {len(df)} rows × {len(df.columns)} columns\n")
            output_lines.append("\n" + df.to_markdown(index=False) + "\n")

            all_tables.append({
                "table_index": idx,
                "rows": len(df),
                "columns": len(df.columns),
                "data": df,
            })
            total_rows += len(df)

            print(f"  Table {idx}: {len(df)} rows × {len(df.columns)} columns")

        output_text = "\n".join(output_lines)

        return {
            "method": "tabula-py",
            "success": True,
            "latency_seconds": round(latency, 2),
            "tables_found": len(all_tables),
            "total_rows": total_rows,
            "output_text": output_text,
            "tables_data": all_tables,
        }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "method": "tabula-py",
            "success": False,
            "error": str(e),
            "latency_seconds": 0,
        }


def main():
    """Run all PDF table extraction tests."""
    # Check if test PDF exists
    if not os.path.exists(TEST_PDF_PATH):
        print(f"Error: Test PDF not found at {TEST_PDF_PATH}")
        print("Please place a PDF at this path.")
        return

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Testing PDF: {TEST_PDF_PATH}")
    print(f"Max pages: {MAX_PAGES_TO_TEST if MAX_PAGES_TO_TEST else 'ALL'}")
    print(f"Output directory: {OUTPUT_DIR}")

    # Run tests
    results = []

    # Test 1: pdfplumber
    result = test_pdfplumber(TEST_PDF_PATH)
    results.append(result)
    if result.get("success") and result.get("output_text"):
        output_file = f"{OUTPUT_DIR}/01_pdfplumber_{timestamp}.md"
        with open(output_file, "w") as f:
            f.write(result["output_text"])
        print(f"Saved to: {output_file}")

    # Test 2: camelot
    result = test_camelot(TEST_PDF_PATH)
    results.append(result)
    if result.get("success") and result.get("output_text"):
        output_file = f"{OUTPUT_DIR}/02_camelot_{timestamp}.md"
        with open(output_file, "w") as f:
            f.write(result["output_text"])
        print(f"Saved to: {output_file}")

    # Test 3: tabula
    result = test_tabula(TEST_PDF_PATH)
    results.append(result)
    if result.get("success") and result.get("output_text"):
        output_file = f"{OUTPUT_DIR}/03_tabula_{timestamp}.md"
        with open(output_file, "w") as f:
            f.write(result["output_text"])
        print(f"Saved to: {output_file}")

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Method':<20} {'Speed':<12} {'Tables':<12} {'Rows':<12} {'Status'}")
    print("-" * 80)

    for result in results:
        method = result["method"]
        speed = f"{result.get('latency_seconds', 0):.2f}s"
        tables = str(result.get("tables_found", 0))
        rows = str(result.get("total_rows", 0))
        status = "SUCCESS" if result.get("success") else f"ERROR: {result.get('error', 'Unknown')}"
        print(f"{method:<20} {speed:<12} {tables:<12} {rows:<12} {status}")

    # Save summary JSON (without DataFrame objects)
    summary_file = f"{OUTPUT_DIR}/summary_{timestamp}.json"
    with open(summary_file, "w") as f:
        # Remove DataFrame objects for JSON serialization
        json_results = []
        for result in results:
            json_result = {k: v for k, v in result.items() if k != "tables_data"}
            json_results.append(json_result)
        json.dump(json_results, f, indent=2, default=str)
    print(f"\nSummary saved to: {summary_file}")


if __name__ == "__main__":
    main()
