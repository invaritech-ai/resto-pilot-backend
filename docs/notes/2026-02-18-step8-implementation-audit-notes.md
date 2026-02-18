# Step 8 Implementation Audit Notes

Date: 2026-02-18  
Reviewer: Codex QA

Tags: #qa #audit #step8 #upload-pipeline #telegram #backend

## Verdict
Step 8 is **not QA-pass** yet. There are production-impacting defects in callback payload sizing, price exponent handling, and OCR page coverage.

## What Is Good
- `record_transaction` now enforces item ownership and uses conflict-safe upsert (`app/services/inventory_service.py:162`, `app/services/inventory_service.py:195`).
- `conf_u` has uploader/member auth guard (`app/telegram/handlers/buttons.py:212`).
- Real PostgreSQL integration fixture exists with savepoint rollback (`tests/conftest.py:23`).
- Staging/parse/files/ocr test modules were added and are substantial.

## P0 Blockers (Fix Before Merge)
1. Callback payloads exceed Telegram 64-byte limit and silently break review UX.
- Builders create oversized data:
- `set_sup:{staging_hex}:{supplier_hex}` (`app/telegram/keyboards.py:67`) is 73 bytes.
- `use_match:{staging_hex}:{idx}:{item_hex}` (`app/telegram/keyboards.py:82`) is ~79 bytes.
- These are used in review keyboards (`app/workers/ocr_tasks.py:342`, `app/telegram/handlers/buttons.py:782`).
- When Telegram rejects keyboard payload, `_send_with_keyboard` swallows error and returns `None` (`app/workers/ocr_tasks.py:455`), then task still commits pending_review (`app/workers/ocr_tasks.py:127`).
- Result: user receives no review message, staging stays pending, flow is stuck.

2. Price exponent math is incompatible with renderer/money service and can crash `/prices`.
- Step 8 writes `price_exp = -4` (`app/services/price_service.py:44`).
- Display path expects non-negative exponent and raises for negative (`app/services/money.py:144`).
- `/prices` and `/list products` call formatter directly (`app/telegram/handlers/commands.py:292`, `app/telegram/handlers/commands.py:357`).
- Result: confirmed price lists can introduce rows that crash price display.

3. Vision PDF fallback drops most pages.
- In chunk loop, only first page of each chunk is parsed (`app/workers/ocr_tasks.py:266`, `app/workers/ocr_tasks.py:268`).
- With default chunk size 3, pages 2 and 3 in each chunk are ignored.
- Result: systematic under-extraction on multi-page PDFs.

## P1 High
1. Authorization is inconsistent across callbacks.
- Auth exists in `conf_u` (`app/telegram/handlers/buttons.py:212`), but not in `doc_type`, `del_u`, `set_sup`, `new_sup`, `use_match`, `mk_item`, `skip_item` (`app/telegram/handlers/buttons.py:141`, `app/telegram/handlers/buttons.py:361`, `app/telegram/handlers/buttons.py:409`, `app/telegram/handlers/buttons.py:467`, `app/telegram/handlers/buttons.py:518`).
- Standardize same uploader/member check for every staging-mutating callback.

2. `set_sup` can proceed after link failure.
- Link errors are swallowed (`app/telegram/handlers/buttons.py:443`), but `staging.supplier_id` is still set (`app/telegram/handlers/buttons.py:446`).
- Result: staging can reference supplier not properly linked to restaurant.

3. Resolution completion check can false-pass with corrupted/incomplete context.
- `_all_resolved` checks only non-None values (`app/telegram/handlers/buttons.py:682`) and does not validate key coverage against current line-item count.

4. Duplicate line-item names can collapse resolution mapping.
- Final map is keyed by `name` (`app/telegram/handlers/buttons.py:702`), so repeated item names overwrite each other.

## Test Status Snapshot
Command run:
- `uv run pytest tests/test_staging_service.py tests/test_item_parser.py tests/test_files_handler.py tests/test_ocr_tasks.py tests/test_buttons.py -q`

Result:
- **33 failed, 64 passed**

Main failure buckets:
1. `tests/test_item_parser.py`: patches `app.llm.item_parser.OpenAI`, but `OpenAI` is imported inside function so patch target is missing.
2. `tests/test_files_handler.py`: helper `_make_ctx_svc(restaurant_id=None)` always returns UUID because of `or`, so “no restaurant” tests are invalid.
3. `tests/test_ocr_tasks.py`: positional assertion bug on kwargs call.
4. `tests/test_buttons.py`: many assertions are stale vs new behavior (price-list path, auth message wording, supplier gate prerequisites, updated resolution flow).

## Backend Triage Plan
1. Fix callback payload strategy first (introduce compact tokens/index mapping).
2. Make keyboard send failures fail-fast (raise) and mark staging error or retry.
3. Replace `price_exp=-4` flow with shared money conversion semantics (`exp >= 0`).
4. Process every PDF page in vision fallback.
5. Apply uniform auth + membership checks across all Step 8 callbacks.
6. Remove broad `except: pass` in `set_sup`; fail closed on link failure.
7. Harden resolution integrity checks (index bounds + full coverage + duplicate-name handling by item index, not name key).
8. Repair/realign failing tests to the current intended behavior.

## Release Gate
Do not mark Step 8 complete until:
- Callback keyboards are Telegram-safe under 64 bytes.
- Price-list confirmation data can be listed by `/prices` without exceptions.
- Multi-page PDF extraction covers all pages.
- Full Step 8 test suite is green.
