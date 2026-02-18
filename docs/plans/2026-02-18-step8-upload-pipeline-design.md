# Step 8: Upload Pipeline — Design Document

**Date:** 2026-02-18  
**Status:** Approved  
**Branch:** `phase-1-step-8`

---

## Scope

File upload → document type classification → OCR/extraction → staging review → anti-pollution gate (supplier + items) → confirmation → DB write.

`/uploads` command is **deferred** (future step).

---

## Build Context

Steps 6 and 7 are complete:
- `FileProcessingStaging` model exists with all needed columns (`file_id`, `file_unique_id`, `mime`, `status`, `document_type`, `extracted_data_json`, `uploaded_by`, `restaurant_id`)
- `InventoryService.fuzzy_match_item()`, `get_or_create_item()`, `confirm_invoice()` are built
- `ContextService.set_active_staging()`, `get_active_staging_id()` are built
- `bot_api.get_file_bytes()` is built
- `keyboards.py` has `upload_review_keyboard()`, `cb_confirm_upload()`, `cb_delete_upload()`, `cb_edit_row()`
- No new Alembic migration needed for this step

---

## Flow

```
User uploads file (image or PDF)
    ↓
files.py: create staging record (status=processing)
Context: active_staging_id = staging.id
    ↓
Bot: "What type of document is this?"
    [1️⃣ Invoice]  [2️⃣ Price List]
    ↓
User taps button
→ doc_type handler: set staging.document_type
→ dispatch Celery task: process_file_task(staging_id_hex, doc_type, chat_id)
→ edit message: "Got it! Processing your invoice..."
    ↓
Celery OCR task:
  PDF:  pdfplumber (all pages, full text) + camelot (all pages, all tables/structures)
        combined into one LLM input → LLM parse
        if line_items < 5: pdf2image → vision model (chunk_size batches) → LLM parse
  Image: base64 → vision model → LLM parse
    ↓
Supplier resolution gate (in OCR task, before sending review message):
  ≥0.8 → auto-set staging.supplier_id
  0.5–0.8 → include set_sup / new_sup buttons in review message
  <0.5 → include new_sup button only
    ↓
staging.extracted_data_json = parsed result
staging.status = pending_review
    ↓
Bot sends review message + keyboard
Context: review_message_id = message_id
    ↓
User resolves supplier (if needed) via set_sup / new_sup handlers
User taps [✅ Confirm All] → conf_u handler
    ↓
conf_u: guard — if staging.supplier_id is null → alert "Please confirm the supplier first"
    ↓
Item resolution gate (invoice only, 3-tier):
  ≥0.8 → auto-resolve (no prompt)
  0.5–0.8 → show [✅ Use "X"] [➕ Create new] per item
  <0.5 → show [➕ Add as new] [⬅️ Skip] per item
    ↓
All auto-resolved? → confirm immediately (edit message to success)
Pending items?  → edit review message to resolution hub
                  Context: pending_item_resolutions, review_message_id (already set)
    ↓
User taps per-item buttons → use_match / mk_item / skip_item handlers
Each handler: store resolution in context → re-edit same message → check if all resolved
    ↓
All resolved → run confirm → edit message to "✅ Invoice confirmed — N items added"
    ↓
staging.status = confirmed
```

---

## Anti-Pollution Gates

### Gate 1: Supplier Resolution

Applied at OCR task completion time, before the review message is sent.

| Score | Action |
|-------|--------|
| ≥ 0.8 | Auto-set `staging.supplier_id` — shown as "Supplier: ABC ✅" in review |
| 0.5–0.8 | `set_sup` + `new_sup` buttons in review message |
| < 0.5 | `new_sup` button only (extracted name shown) |

`conf_u` hard-blocks until `staging.supplier_id` is set.

### Gate 2: Item Resolution (Invoice only)

Applied when `conf_u` is tapped.

| Score | Action |
|-------|--------|
| ≥ 0.8 | Auto-resolve to matched `inventory_item.id` |
| 0.5–0.8 | Show `[✅ Use "Chicken Breast"]  [➕ Create "Chicken Brst"]` |
| < 0.5 | Show `[➕ Add as new item]  [⬅️ Skip this row]` |

No item gate for **price list** confirmation — we upsert prices, not inventory.

### Edit-in-Place UX (Approach A)

All pending item resolutions in one message (the edited review message). After each button tap, the same message is re-edited to mark the item resolved (✓) and update remaining buttons. When all resolved, message is replaced with final success state. No message flood.

`review_message_id` is stored in `user.context` when the OCR task sends the review message — used by all resolution handlers for `edit_message_text()`.

Cap: show up to 10 pending items per message. If somehow >10 pending → show first 10 + "N more items after confirming."

---

## PDF Extraction Pipeline

```
pdf_bytes
    │
    ├─ ALWAYS: pdfplumber (all pages)
    │   └─ Extracts full text: supplier header, contact info, lead times, terms
    │
    ├─ ALWAYS: camelot (all pages, all extractable structures)
    │   └─ Extracts tables and structured data: line items, prices, quantities
    │
    ├─ Combine into one LLM input string:
    │   "FULL TEXT:\n{pdfplumber_text}\n\nSTRUCTURED TABLES:\n{camelot_output}"
    │   └─ ONE LLM call → full structured JSON (supplier header + line_items)
    │
    ├─ if len(line_items) >= 5 → DONE ✅
    │
    └─ Else vision fallback:
        └─ pdf2image.convert_from_bytes(dpi=150) → PIL images per page
        └─ Process in batches of settings.vision_pdf_chunk_size (default 3)
        └─ Each batch → vision model (multi-image message) → partial structured result
        └─ Merge all partial results → deduplicate items by name → final JSON
```

**LLM input strategy:** Pass everything verbatim to the parser. More context = better inference. The prompt handles column inference, supplier/body separation, and unit normalization. No heuristic column mapping.

---

## Vision / LLM Stack

All LLM calls use the `openai` Python client (added to `pyproject.toml`):

```python
client = OpenAI(
    api_key=settings.vision_api_key or settings.openai_api_key,
    base_url=settings.vision_base_url or settings.openai_base_url,
)
model = settings.vision_model or settings.openai_model
```

Transport-agnostic: works with OpenAI native, OpenRouter (Gemini Flash, Claude, etc.), or any OpenAI-compatible endpoint.

---

## `extracted_data_json` Schema

```json
// Invoice
{
  "supplier": "ABC Wholesalers",
  "supplier_contact_name": "John Smith",
  "supplier_phone": "+1-800-555-0100",
  "supplier_email": "orders@abc.com",
  "invoice_date": "2026-02-15",
  "invoice_number": "INV-2023",
  "currency": "USD",
  "line_items": [
    {"name": "Chicken Breast", "qty": 10.0, "unit": "kg", "unit_price": 8.50, "amount": 85.00},
    {"name": "Olive Oil",      "qty": 2.0,  "unit": "L",  "unit_price": 3.20, "amount": 6.40}
  ]
}

// Price list
{
  "supplier": "ABC Wholesalers",
  "supplier_contact_name": "Jane Doe",
  "supplier_phone": "+1-800-555-0101",
  "supplier_email": "pricing@abc.com",
  "lead_time": "3-5 business days",
  "effective_date": "2026-02-01",
  "currency": "USD",
  "line_items": [
    {"name": "Chicken Breast", "unit": "kg", "unit_price": 8.50},
    {"name": "Olive Oil",      "unit": "L",  "unit_price": 3.20}
  ]
}
```

All supplier contact fields are nullable. Amount cross-check: `abs(qty × unit_price - amount) > 0.01` → ⚠️ flag in review (user can still confirm).

---

## Confirmation Side Effects

### Invoice confirmation writes to:
1. `inventory_transactions` — one credit row per resolved line item
2. `inventory_balances` — upserted atomically with each transaction
3. `suppliers` — null-fill `contact_name`, `phone`, `email` from extracted data
4. `file_processing_staging.status = "confirmed"`

**Does NOT write to `supplier_prices`.**  
Invoice purchase prices live only in `inventory_transactions.unit_price` — enabling future purchase-vs-catalogue price comparison.

### Price list confirmation writes to:
1. `supplier_price_lists` — one record per confirmation
2. `supplier_prices` — upserted per line item (immediately visible in `/products`, `/prices`)
3. `suppliers` — null-fill `contact_name`, `phone`, `email`, `default_currency`; `notes` updated with lead time if present and currently null
4. `file_processing_staging.status = "confirmed"`

### Supplier enrichment policy
**Null-fill only** — never overwrite existing data. Corrections via future `/edit supplier` command.

---

## New Context Fields

```python
user.context = {
    # existing
    "active_staging_id": "uuid-str",

    # new in step 8
    "pending_item_resolutions": {
        "0": "item-uuid-str",   # resolved to existing item
        "1": "new",             # to be created as new item
        "2": "skip",            # user chose to skip
        "3": None,              # still pending
    },
    "review_message_id": 12345,  # Telegram message_id for edit-in-place
}
```

Both new fields added to `_NAV_FIELDS` in `context_service.py` — cleared by `clear_navigation()`.

---

## New Button Callback Actions

| Action | Format | Purpose |
|--------|--------|---------|
| `doc_type` | `doc_type:{staging_hex}:invoice\|price_list` | Set document type |
| `use_match` | `use_match:{staging_hex}:{idx}:{item_hex}` | Accept fuzzy match suggestion |
| `mk_item` | `mk_item:{staging_hex}:{idx}` | Create new item (overriding suggestion) |
| `skip_item` | `skip_item:{staging_hex}:{idx}` | Skip line item |
| `set_sup` | `set_sup:{staging_hex}:{supplier_hex}` | *(stub → now implemented)* |
| `new_sup` | `new_sup:{staging_hex}` | *(stub → now implemented)* |

---

## Files Created / Modified

### New files
```
app/llm/__init__.py
app/llm/item_parser.py              — invoice + price list LLM extraction prompts + parse
app/services/staging_service.py     — CRUD for FileProcessingStaging
app/services/price_service.py       — confirm_price_list() + supplier enrichment
app/telegram/handlers/files.py      — Priority 4 file upload handler
app/workers/ocr_tasks.py            — Celery task: extract → parse → send review
```

### Modified files
```
pyproject.toml                       — add openai>=1.0.0
app/telegram/keyboards.py            — add doc_type, use_match, mk_item, skip_item builders
app/telegram/handlers/buttons.py     — doc_type handler; upgraded conf_u (3-tier + supplier gate);
                                       use_match/mk_item/skip_item; implement del_u, set_sup, new_sup
app/workers/celery_app.py            — add app.workers.ocr_tasks to include list
app/workers/telegram_tasks.py        — replace _handle_file stub with files.handle()
app/services/context_service.py      — add pending_item_resolutions + review_message_id to _NAV_FIELDS
```

### New tests
```
tests/test_staging_service.py
tests/test_item_parser.py            — mocked OpenAI responses
tests/test_files_handler.py
tests/test_ocr_tasks.py              — mocked extraction + mocked send_message
tests/test_upload_confirm.py         — invoice + price list full confirm flows + supplier gate
```

---

## Implementation Order

```
1. Design doc (this file) committed first
2. pyproject.toml — add openai
3. app/services/staging_service.py
4. app/llm/__init__.py + app/llm/item_parser.py
5. app/services/price_service.py
6. app/workers/ocr_tasks.py
7. app/workers/celery_app.py — add include
8. app/telegram/keyboards.py — new callbacks
9. app/telegram/handlers/files.py
10. app/telegram/handlers/buttons.py — upgrade
11. app/workers/telegram_tasks.py — wire
12. app/services/context_service.py — nav fields
13. Tests (5 files)
```

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| Celery OCR task fails | `staging.status = "error"`, `error_message = str(exc)`, bot: "⚠️ Processing failed. Please try uploading again." |
| `conf_u` when supplier not set | `answer_callback_query(show_alert=True, text="Please confirm the supplier first.")` |
| `conf_u` tapped twice | `staging.status != "pending_review"` check at top → "This upload has already been processed." |
| OCR extracts < 5 items (went to vision) | Flag in review: "Only N items found — does this look right?" |
| Amount mismatch > 1% | ⚠️ per row in review; user can still confirm |
| Telegram file expired | `TelegramFileExpiredError` → staging.status=error → "File expired. Please upload again." |
| camelot/pdfplumber raise on corrupt PDF | Catch exception, fall through to vision fallback |

---

## Acceptance Criteria

| Scenario | Pass Criteria |
|----------|---------------|
| Upload JPEG invoice | Staging created, doc type keyboard shown |
| Upload PDF price list | Staging created, doc type keyboard shown |
| Select "Invoice" | Celery task dispatched, "Processing..." shown |
| Extraction complete | Review message with line items + keyboard sent |
| Supplier auto-matched (≥0.8) | Review shows "Supplier: ABC ✅" |
| Supplier 0.5–0.8 | Review shows set_sup + new_sup buttons |
| Supplier <0.5 / missing | Review shows new_sup button only |
| Confirm invoice (all known items) | Transactions + balances created, status=confirmed |
| Confirm invoice (new/partial items) | Resolution hub shown, resolutions collected, confirm after all resolved |
| Confirm price list | `supplier_prices` upserted, supplier null-filled, status=confirmed |
| Cancel upload | `del_u` → status=cancelled, context cleared, message updated |
| OCR fails | status=error, user notified |
| PDF < 5 items | Vision fallback fires, result merged |
| New supplier via `new_sup` | Supplier created + linked, staging.supplier_id set, confirm proceeds |
