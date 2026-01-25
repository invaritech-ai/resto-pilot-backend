#!/usr/bin/env python3
"""
Example: Upload supplier price lists via API for multiple restaurants.

This demonstrates the complete workflow for side-loading supplier files:
1. Upload price list PDF for Restaurant A
2. Upload same supplier's file for Restaurant B
3. Monitor processing status
4. Retrieve and confirm extracted data

The system will:
- First upload: Create supplier + items + prices for Restaurant A
- Second upload: Reuse supplier, create new prices for Restaurant B
- Track price history over time with dates
"""

import requests
import time
from pathlib import Path


# API Configuration
API_BASE_URL = "http://localhost:8000/api/v1"

# Sample UUIDs (replace with actual values from your database)
RESTAURANT_A_ID = "550e8400-e29b-41d4-a716-446655440000"
RESTAURANT_B_ID = "660e8400-e29b-41d4-a716-446655440001"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"


def upload_supplier_file(
    file_path: str,
    restaurant_id: str,
    user_id: str,
    supplier_id: str | None = None
) -> dict:
    """
    Upload a supplier price list file via API.

    Args:
        file_path: Path to PDF file
        restaurant_id: Restaurant UUID
        user_id: User UUID
        supplier_id: Optional existing supplier UUID

    Returns:
        {
            "run_id": "...",
            "status": "processing",
            "status_url": "/api/v1/files/{run_id}/status"
        }
    """
    url = f"{API_BASE_URL}/files/upload"

    # Read file
    with open(file_path, 'rb') as f:
        files = {'file': (Path(file_path).name, f, 'application/pdf')}

        # Form data
        data = {
            'restaurant_id': restaurant_id,
            'user_id': user_id,
            'processing_type': 'price_list',
        }

        if supplier_id:
            data['supplier_id'] = supplier_id

        print(f"\n📤 Uploading: {Path(file_path).name}")
        print(f"   Restaurant: {restaurant_id}")
        if supplier_id:
            print(f"   Supplier: {supplier_id}")

        response = requests.post(url, files=files, data=data)
        response.raise_for_status()

        result = response.json()
        print(f"✅ Upload successful!")
        print(f"   Run ID: {result['run_id']}")

        return result


def check_status(run_id: str) -> dict:
    """
    Check processing status.

    Returns:
        {
            "run_id": "...",
            "status": "processing",
            "progress_percentage": 45.0,
            "pages_processed": 3,
            "pages_total": 6,
            "estimated_seconds_remaining": 15
        }
    """
    url = f"{API_BASE_URL}/files/{run_id}/status"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def wait_for_completion(run_id: str, poll_interval: int = 2) -> dict:
    """
    Poll status endpoint until processing completes.

    Args:
        run_id: FileProcessingRuns UUID
        poll_interval: Seconds between polls

    Returns:
        Final status dict with staging_id
    """
    print(f"\n⏳ Waiting for processing to complete...")

    while True:
        status = check_status(run_id)

        if status['status'] == 'completed':
            print(f"✅ Processing complete!")
            print(f"   Staging ID: {status.get('staging_id')}")
            return status
        elif status['status'] == 'failed':
            print(f"❌ Processing failed: {status.get('error_message')}")
            raise Exception(f"Processing failed: {status.get('error_message')}")
        else:
            # Still processing
            pct = status.get('progress_percentage', 0)
            pages = f"{status.get('pages_processed', 0)}/{status.get('pages_total', '?')}"
            print(f"   Progress: {pct:.0f}% ({pages} pages)")
            time.sleep(poll_interval)


def get_extracted_data(staging_id: str) -> dict:
    """
    Retrieve extracted data from staging.

    Returns:
        {
            "staging_id": "...",
            "status": "pending_review",
            "processing_type": "price_list",
            "extracted_data": {
                "supplier_name": "...",
                "items": [...]
            }
        }
    """
    url = f"{API_BASE_URL}/files/{staging_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def confirm_data(staging_id: str, edits: dict | None = None) -> dict:
    """
    Confirm extracted data and save to final tables.

    Args:
        staging_id: FileProcessingStaging UUID
        edits: Optional corrections to apply

    Returns:
        {
            "status": "confirmed",
            "staging_id": "...",
            "message": "..."
        }
    """
    url = f"{API_BASE_URL}/files/{staging_id}/confirm"

    payload = {}
    if edits:
        payload['edits'] = edits

    response = requests.post(url, json=payload if payload else None)
    response.raise_for_status()
    return response.json()


def example_workflow():
    """
    Complete example: Upload same supplier's price list for multiple restaurants.
    """
    print("=" * 80)
    print("SUPPLIER PRICE LIST UPLOAD - MULTI-RESTAURANT EXAMPLE")
    print("=" * 80)

    # Example file path (replace with actual path)
    price_list_file = "/Users/avi/Downloads/Price List.pdf"

    if not Path(price_list_file).exists():
        print(f"\n❌ File not found: {price_list_file}")
        print("Please update the file path in this script.")
        return

    # ========================================
    # SCENARIO 1: First upload for Restaurant A
    # This will create: supplier + items + prices
    # ========================================

    print("\n" + "=" * 80)
    print("SCENARIO 1: Upload price list for Restaurant A (first time)")
    print("=" * 80)

    # Upload
    upload_result_a = upload_supplier_file(
        file_path=price_list_file,
        restaurant_id=RESTAURANT_A_ID,
        user_id=USER_ID,
        supplier_id=None  # No existing supplier - will create new
    )

    # Wait for completion
    status_a = wait_for_completion(upload_result_a['run_id'])

    # Get extracted data
    data_a = get_extracted_data(status_a['staging_id'])
    print(f"\n📋 Extracted Data:")
    print(f"   Supplier: {data_a['extracted_data'].get('supplier_name', 'N/A')}")
    print(f"   Currency: {data_a['extracted_data'].get('currency', 'N/A')}")
    print(f"   Items: {len(data_a['extracted_data'].get('items', []))}")

    # Confirm (this saves to final tables)
    print(f"\n✅ Confirming data for Restaurant A...")
    confirm_result_a = confirm_data(status_a['staging_id'])
    print(f"   Status: {confirm_result_a['status']}")
    print(f"   Message: {confirm_result_a.get('message', 'Confirmed')}")

    # ========================================
    # SCENARIO 2: Upload for Restaurant B (same supplier)
    # This will: link to existing supplier + create new prices
    # ========================================

    print("\n" + "=" * 80)
    print("SCENARIO 2: Upload same supplier for Restaurant B")
    print("=" * 80)
    print("The system will:")
    print("  1. Match supplier by name (case-insensitive)")
    print("  2. Reuse existing inventory items")
    print("  3. Create new price records for Restaurant B")
    print("  4. Track price history with dates")

    # Upload (note: same file, different restaurant)
    upload_result_b = upload_supplier_file(
        file_path=price_list_file,
        restaurant_id=RESTAURANT_B_ID,
        user_id=USER_ID,
        supplier_id=None  # System will match by name
    )

    # Wait for completion
    status_b = wait_for_completion(upload_result_b['run_id'])

    # Get extracted data
    data_b = get_extracted_data(status_b['staging_id'])
    print(f"\n📋 Extracted Data:")
    print(f"   Supplier: {data_b['extracted_data'].get('supplier_name', 'N/A')}")
    print(f"   Items: {len(data_b['extracted_data'].get('items', []))}")

    # Confirm
    print(f"\n✅ Confirming data for Restaurant B...")
    confirm_result_b = confirm_data(status_b['staging_id'])
    print(f"   Status: {confirm_result_b['status']}")

    # ========================================
    # SUMMARY
    # ========================================

    print("\n" + "=" * 80)
    print("WORKFLOW COMPLETE")
    print("=" * 80)
    print("\nWhat happened in the database:")
    print("  1. Supplier created once (matched by name)")
    print("  2. Inventory items created/matched")
    print("  3. Price history maintained:")
    print("     - Restaurant A: prices with valid_from = upload date")
    print("     - Restaurant B: separate prices with valid_from = upload date")
    print("  4. Future uploads will add new price records")
    print("  5. Old prices remain with valid_to still NULL (manually set if needed)")

    print("\nNext steps:")
    print("  - Check suppliers table for the new supplier")
    print("  - Check supplier_items for linked inventory")
    print("  - Check supplier_prices for price history")
    print("  - Upload monthly updates to track price changes over time")


def example_monthly_update():
    """
    Example: Monthly price update for existing supplier.
    """
    print("\n" + "=" * 80)
    print("MONTHLY UPDATE EXAMPLE")
    print("=" * 80)

    price_list_file = "/Users/avi/Downloads/Price List.pdf"

    if not Path(price_list_file).exists():
        print(f"\n❌ File not found: {price_list_file}")
        return

    print("\nMonthly update scenario:")
    print("  - Same supplier sends updated price list")
    print("  - System matches supplier by name")
    print("  - Creates new price records with current date")
    print("  - Previous prices remain in database (price history)")

    # Upload with current date
    upload_result = upload_supplier_file(
        file_path=price_list_file,
        restaurant_id=RESTAURANT_A_ID,
        user_id=USER_ID,
        supplier_id=None  # System matches by name
    )

    status = wait_for_completion(upload_result['run_id'])
    data = get_extracted_data(status['staging_id'])

    print(f"\n📋 Updated prices extracted:")
    print(f"   Items: {len(data['extracted_data'].get('items', []))}")

    # Optionally apply corrections before confirming
    edits = {
        # Example: correct a supplier name typo
        # "supplier_name": "Corrected Supplier Name"
    }

    print(f"\n✅ Confirming monthly update...")
    confirm_result = confirm_data(status['staging_id'], edits=edits if edits else None)
    print(f"   Status: {confirm_result['status']}")

    print("\n💡 Price history now includes:")
    print("   - Original prices from first upload")
    print("   - Updated prices from this upload")
    print("   - Both tracked with valid_from dates")


if __name__ == "__main__":
    print("\n")
    print("This is an example script demonstrating API usage.")
    print("Update the RESTAURANT_* and USER_ID constants with actual UUIDs.")
    print("Update the file path to point to your test PDF.")
    print("\n")
    print("To run the example:")
    print("  1. Ensure API server is running (uvicorn app.main:app)")
    print("  2. Ensure Celery worker is running")
    print("  3. Update UUIDs and file path in this script")
    print("  4. Run: python example_api_supplier_upload.py")
    print("\n")

    # Uncomment to run the example:
    # example_workflow()
    # example_monthly_update()
