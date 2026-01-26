# File Processing Patch Plan (Page Jobs + In-Flight OCR Retries + Shared Suppliers)

## Goals and constraints
- Treat each page as a separate job; OCR + extraction + snapshotting are done per page.
- OCR retries happen inside the same job using in-memory bytes only (no file persistence).
- Extraction retries can reuse saved OCR text from `FileProcessingSteps`.
- Run completes when all page jobs are in terminal state; merge is deterministic code only.
- API and Telegram both use the same processing pipeline (single coordinator + page jobs).
- Suppliers are independent entities; restaurants link to suppliers so price updates apply across restaurants.

## Phase 0: Decisions and defaults
- Retry policy (initial defaults):
  - OCR: 2-3 attempts with exponential backoff + jitter.
  - Extraction: 2 attempts with JSON repair prompt.
- Mark a page as failed if all retries are exhausted; do not fail the entire run.
- Terminal page statuses: `completed`, `failed_ocr`, `failed_extraction`.
- Run status on partial failure: keep `status="completed"` and set `error_message` summary
  (e.g., "3 pages failed: [2, 7, 18]").
- Page bytes transport:
  - Prefer passing a pointer to shared storage (S3/DB blob) when broker payload limits apply.
  - Allow base64 in task args only when safely under broker limits; enforce size guardrails.
- Define restaurant model:
  - `restaurants` represent business locations, so use `restaurant_suppliers`.
- Define supplier identity rules:
  - Normalized name for de-duplication.
  - Whether supplier linkage is global or scoped to a parent org/brand.

## Phase 1: Schema and models
1) Add `FileProcessingPageJobs` model
   - File: `app/db/models/file_processing_page_jobs.py`
   - Fields:
     - `id` (UUID, PK)
     - `run_id` (FK -> `file_processing_runs.id`, indexed)
     - `page_index` (int, 1-indexed, indexed)
     - `status` (enum: `pending`, `processing`, `completed`, `failed_ocr`, `failed_extraction`)
     - `ocr_retries` (int, default 0)
     - `extraction_retries` (int, default 0)
     - `error_message` (string, nullable)
     - `created_at`, `updated_at`
   - Constraints:
     - Unique `(run_id, page_index)` to enforce idempotency.

2) Add run metadata for unified orchestration
   - File: `app/db/models/file_processing_runs.py`
   - Fields:
     - `source` (enum: `api`, `telegram`)
     - `chat_id` (bigint, nullable) for Telegram notifications
     - `session_id` (UUID, nullable) for telemetry linking
     - `supplier_id` (UUID, nullable) if selected at intake

3) Supplier refactor (global suppliers + restaurant links)
   - Update `suppliers` table:
     - Remove `restaurant_id` (supplier is global).
     - Add `name_normalized` (indexed) to support matching/dedup.
   - Add join table:
     - `restaurant_suppliers`.
     - Fields: `restaurant_id`, `supplier_id`, `status`, `account_number`,
       `default_currency`, `lead_time_days`, `notes`, `created_at`, `updated_at`.
     - Unique `(restaurant_id, supplier_id)`.
   - Add mapping table for restaurant product linkage:
     - `supplier_item_products` with `supplier_item_id`, `restaurant_id`, `product_id`.
     - Unique `(supplier_item_id, restaurant_id)`.
   - Ensure `FileProcessingStaging` includes `supplier_id` if missing (model + migration).

4) Alembic migrations
   - Create `file_processing_page_jobs`, new enums, and indexes.
   - Add run metadata columns and supplier join/mapping tables.
   - Migrate existing suppliers:
     - Create global supplier rows, insert link rows per restaurant.
     - Backfill `name_normalized`.

5) Register models
   - Add imports in `app/db/models/__init__.py`.

## Phase 2: Intake (API + Telegram -> coordinator)
1) Update API upload to enqueue coordinator only
   - File: `app/api/v1/routes/files.py`
   - Keep validation + run/staging creation.
   - Enqueue coordinator task with `source="api"` and file payload (or storage pointer).

2) Update Telegram tasks to enqueue coordinator
   - File: `app/workers/file_processing_tasks.py`
   - Keep run/staging creation and file download.
   - Enqueue coordinator with `source="telegram"`, `chat_id`, `session_id`, and file bytes.
   - Do not do OCR/extraction in the Telegram task anymore.

3) Task idempotency
   - Use deterministic task IDs like `run_id:page_index` when enqueueing.
   - Coordinator should skip page creation if `(run_id, page_index)` exists.

## Phase 3: Coordinator task (page job creation)
1) Create coordinator task
   - File: `app/workers/file_processing_tasks.py`
   - Option A: repurpose `process_file_api_task` to become coordinator.
   - Option B: create new `enqueue_page_jobs_task` and call from API/Telegram intake.

2) Coordinator logic (pseudocode)
```python
def enqueue_page_jobs_task(run_id, file_payload, mime_type, filename):
    file_bytes = resolve_file_bytes(file_payload)  # base64 or storage pointer
    run = db.get(FileProcessingRuns, UUID(run_id))
    run.current_stage = "pdf_to_images" or "image_to_page"
    db.commit()

    if mime_type == "application/pdf":
        pages = _convert_pdf_pages_to_images(file_bytes)
    else:
        pages = [(1, file_bytes)]

    run.pages_total = len(pages)
    run.pages_processed = 0
    run.current_stage = "page_jobs_enqueued"
    db.commit()

    for page_index, page_bytes in pages:
        create FileProcessingPageJobs(run_id, page_index, status="pending")
        enqueue process_page_job_task(run_id, page_index, page_bytes, mime_type)
```

3) Broker safety
   - If page payload exceeds broker limits, store page bytes in shared storage and pass a key.
   - Keep retry logic in-page; do not persist bytes for OCR retries beyond the task.

## Phase 4: Per-page processing task
1) Add `process_page_job_task`
   - File: `app/workers/file_processing_tasks.py`
   - Input: `run_id`, `page_index`, `page_payload`, `mime_type`.
   - Steps:
     1. Load page job; if `status` in terminal states, return.
     2. Mark `status=processing` (compare-and-set to avoid double processing).
     3. OCR with retry helper; on success, save snapshot (`stage="ocr"`).
     4. Extraction with retry helper; on success, save snapshot (`stage="extraction"`).
     5. Mark page job as `completed`.
     6. Call `maybe_finalize_run(run_id)`.

2) Extend snapshot saving for failure states
   - File: `app/processing/page_processor.py`
   - Extend `save_page_snapshot` to accept `error_message` and `status="failed"`.
   - Store `error_message` in `FileProcessingSteps`.

3) OCR retry helper
   - File: `app/processing/page_processor.py` or `app/workers/file_processing_tasks.py`
   - Function `ocr_page_with_retries(page_bytes, page_index, total_pages, settings)`
   - Retries on exceptions from `ocr_page_to_markdown`.
   - Use backoff with jitter (e.g., 1s, 2s, 4s).

4) Extraction retry + repair helper
   - Function `extract_json_with_retries(markdown_text, page_index, processing_type, db_schema, settings)`
   - Steps:
     - Call `extract_json_from_markdown`.
     - Validate: `json.loads(result.content)` must succeed.
     - If JSON parse fails, retry with repair prompt injection.
   - Requires small change in `extract_json_from_markdown` to accept `repair_hint`
     or a new helper that builds an alternate prompt.

5) Page job failure outcomes
   - If OCR fails after retries:
     - Save snapshot (`stage="ocr"`, `status="failed"`, `error_message=...`)
     - Update page job `status="failed_ocr"`.
   - If extraction fails after retries:
     - Save snapshot (`stage="extraction"`, `status="failed"`, `error_message=...`)
     - Update page job `status="failed_extraction"`.

## Phase 5: Synchronization, merge, and notifications
1) Add `maybe_finalize_run`
   - File: `app/workers/file_processing_tasks.py`
   - Logic:
     - Query counts of page jobs by status for `run_id`.
     - If all pages are terminal, attempt to lock finalization:
       - Update `FileProcessingRuns.current_stage` to `merge_pending` (compare-and-set).
     - If lock succeeds, enqueue `finalize_run_task`.

2) Add `finalize_run_task`
   - File: `app/workers/file_processing_tasks.py`
   - Steps:
     - Merge using existing `merge_page_results(run_id, db)`.
     - Update `FileProcessingStaging.extracted_data_json`.
     - Set `staging.status="pending_review"`.
     - Set `run.status="completed"`, `run.current_stage="finalize"`, `finished_at=now`.
     - Populate `run.error_message` if any failed pages.
     - If `run.source == "telegram"`, send chat status/pending actions using
       existing message helpers and `run.chat_id`.

## Phase 6: Status reporting
1) Extend `get_processing_status`
   - File: `app/db/queries/file_processing.py`
   - Compute:
     - `pages_completed`
     - `pages_failed`
     - `pages_processing`
   - Replace `pages_processed` with `pages_completed` (or keep both).
   - Update `progress_percentage` based on completed pages.

2) Optional: expose page job details
   - Add new API endpoint: `/files/{run_id}/pages`
   - Returns list of page job statuses + error messages.

## Phase 7: Supplier tooling updates
1) Update supplier tools/queries to use join table
   - File: `app/ai/db_tools/suppliers.py`
   - Scope supplier listing and access via `restaurant_suppliers`.

2) Update file processing supplier resolution
   - File: `app/workers/file_processing_tasks.py`, `app/ai/db_tools/file_processing.py`
   - When supplier name is found, map to global supplier and ensure the
     restaurant link exists.

3) Update item mapping logic
   - File: `app/ai/db_tools/file_processing.py`
   - Use `supplier_item_products` for restaurant-specific product matching.

## Phase 8: Cleanup
1) Add periodic cleanup task
   - File: `app/workers/file_processing_tasks.py`
   - Delete `FileProcessingPageJobs` and `FileProcessingSteps`
     where run finished and older than TTL (e.g., `staging.expires_at`).
   - Add to Celery beat schedule in `app/workers/celery_app.py`.

## Phase 9: Tests
1) Unit tests for page job creation
   - Ensure correct page count for PDFs and images.
2) OCR retry behavior
   - Simulate OCR exceptions; verify retries and final status.
3) Extraction retry and JSON repair
   - Simulate invalid JSON; verify retry and failure path.
4) Finalization lock
   - Ensure only one `finalize_run_task` runs under concurrent page completions.
5) Status aggregation
   - Verify progress and counts from page job table.
6) Supplier refactor
   - Migrate suppliers + link tables; verify global supplier reuse across restaurants.

## Notes on synchronization
- Per-page tasks can run in parallel without shared state, as all writes are per-page.
- The only shared mutation is finalization; use compare-and-set on `current_stage`
  to avoid multiple merges.
- Staging should be written only once by `finalize_run_task` to avoid races.
