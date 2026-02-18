# Implementation Plan: Reads → Inventory Model → Upload Pipeline

**Date:** 2026-02-18
**Design doc:** `docs/plans/2026-02-18-reads-and-inventory-design.md`
**Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, Celery, Neon PostgreSQL
**Test command:** `uv run pytest`

---

## Phase 1 — Read Commands (branch: `phase-1-step-6`)

### Goal
Implement all 8 read slash commands. No new DB tables. `/inventory` and `/balance` return stubs.

---

### Ticket R-1: `/profile` command

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Get `user.full_name`, `user.username`
2. Get active restaurant via `_require_active_restaurant()`
3. Get user's membership (role, joined_at) via `RestaurantService.list_for_user()`
4. Format and send

**Output:**
```
👤 Profile

Name: John Smith
Username: @johnsmith
Restaurant: The Blue Bistro
Role: Owner
Member since: Feb 2026
```

**Tests:** `tests/test_commands.py` — mock user + restaurant, assert message contains name/restaurant/role

---

### Ticket R-2: `/team` command

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Require active restaurant
2. `RestaurantService.list_members(restaurant_id=...)` → list of (User, RestaurantUser)
3. Paginate (10 per page), store IDs in `set_numbered_items()`
4. Format: name, role (owner/member), joined date

**Output:**
```
👥 Team — The Blue Bistro

1. John Smith (Owner) — since Feb 2026
2. Maria Garcia (Member) — since Feb 2026

1 member total.
```

**Invites:** stub "No pending invites" for now (invite system not yet built)

**Tests:** empty team, single member, pagination at 11+ members

---

### Ticket R-3: `/outlets` command

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. `RestaurantService.list_for_user(user_id=user.id)` — no active restaurant required
2. Paginate, store IDs in `set_numbered_items()`
3. Mark active restaurant with ✅

**Output:**
```
🏪 Your Restaurants

1. ✅ The Blue Bistro (active)
2. Corner Cafe

Reply #N to switch.
```

**On `#N` reply:** set active restaurant (reuse switch logic)

**Tests:** no restaurants, single restaurant (auto-active), multiple restaurants

---

### Ticket R-4: `/products` command

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Require active restaurant
2. Query `supplier_prices` joined with `suppliers` where `restaurant_id` matches (via `restaurant_suppliers`)
3. Paginate 10 per page, grouped by supplier name
4. Store `supplier_price.id` list in `set_numbered_items()`

**Output:**
```
📦 Products

ABC Wholesalers:
1. Chicken Breast — $8.50/kg
2. Olive Oil — $3.20/L

Fresh Co:
3. Tomatoes — $2.10/kg

Page 1/2  [Next ▶]
```

**Tests:** no price data, single supplier, multi-supplier pagination

---

### Ticket R-5: `/prices <supplier>` command

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Require active restaurant
2. `args` = supplier name search string
3. `SupplierService.fuzzy_search(args, threshold=0.6)` → find supplier
4. Query `supplier_prices` for that supplier + restaurant
5. Paginate, show item name + current price + effective date

**Output:**
```
💰 Prices — ABC Wholesalers

1. Chicken Breast — $8.50/kg (updated Feb 15)
2. Olive Oil — $3.20/L (updated Feb 15)
3. Tomatoes — $2.10/kg (updated Feb 10)
```

**Edge cases:** no args → "Usage: /prices ABC Wholesalers"; no match → "No supplier found matching X"

**Tests:** no args, no match, single supplier with prices, pagination

---

### Ticket R-6: `/inventory` command (stub)

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Require active restaurant
2. Return stub: "No inventory data yet. Upload an invoice to get started."

*Will be wired to real data in step 7.*

**Tests:** stub response returned

---

### Ticket R-7: `/balance` command (stub)

**File:** `app/telegram/handlers/commands.py`

**Handler logic:**
1. Require active restaurant
2. Return stub: "No inventory data yet."

*Will be wired to real data in step 7.*

**Tests:** stub response returned

---

### Ticket R-8: Update `/help` text

Update `HELP_TEXT` in `commands.py` to include all new commands.

---

### Ticket R-9: Tests

**File:** `tests/test_commands.py`

Cover all new handlers. Minimum: happy path + empty state + no active restaurant for each.
Use existing pattern: mock `send_message`, mock DB session, assert call args.

**Run:** `uv run pytest tests/test_commands.py -v`

---

### Ticket R-10: Commit + merge

```bash
git add app/telegram/handlers/commands.py tests/test_commands.py
git commit -m "feat(step-6): implement all read commands"
# merge phase-1-step-6 → develop, delete branch
```

---

## Phase 2 — Inventory Data Model (branch: `phase-1-step-7`)

### Goal
New tables, migration, `InventoryService`, wire `/inventory` and `/balance` to real data.

---

### Ticket I-1: DB models

**Files to create:**
- `app/db/models/inventory_item.py`
- `app/db/models/inventory_transaction.py`
- `app/db/models/inventory_balance.py`

Follow existing model patterns (UUID PK, `Base`, `Mapped`, `mapped_column`).

`InventoryItem`:
- `id`, `restaurant_id` FK, `name`, `name_lower` (generated), `unit`, `supplier_id` FK nullable, `created_at`
- GIN index on `name_lower` using `gin_trgm_ops`
- UNIQUE `(restaurant_id, name_lower)`

`InventoryTransaction`:
- `id`, `restaurant_id` FK, `item_id` FK, `txn_type` CHECK IN ('credit','debit')
- `quantity` NUMERIC(12,3), `unit_price` NUMERIC(12,4) nullable, `amount` NUMERIC(12,2) nullable
- `source` CHECK IN ('invoice','manual'), `staging_id` FK nullable, `notes`, `created_by` FK, `created_at`

`InventoryBalance`:
- `id`, `restaurant_id` FK, `item_id` FK, `balance` NUMERIC(12,3), `last_txn_id` FK, `updated_at`
- UNIQUE `(restaurant_id, item_id)`

---

### Ticket I-2: Alter existing tables

Add to migration (same Alembic revision as I-3):
```sql
ALTER TABLE suppliers ADD COLUMN default_currency CHAR(3);
ALTER TABLE file_processing_staging
    ADD COLUMN document_type TEXT CHECK (document_type IN ('invoice', 'price_list'));
```

---

### Ticket I-3: Alembic migration

```bash
uv run alembic revision --autogenerate -m "inventory_tables_and_staging_columns"
# Review generated migration, adjust if needed
uv run alembic upgrade head
```

Verify GIN index and CHECK constraints are present in the generated DDL.

---

### Ticket I-4: `InventoryService`

**File:** `app/services/inventory_service.py`

Methods:

| Method | Signature | Notes |
|--------|-----------|-------|
| `list_items` | `(restaurant_id, offset, limit)` → `list[(InventoryItem, Decimal balance)]` | JOIN with inventory_balances |
| `get_item_detail` | `(item_id)` → item + last transaction | For `#N` drill-down |
| `get_balance_summary` | `(restaurant_id)` → `{total, zero_stock, negative}` | For `/balance` |
| `record_transaction` | `(restaurant_id, item_id, txn_type, quantity, ...)` → `InventoryTransaction` | Atomic with balance upsert |
| `fuzzy_match_item` | `(restaurant_id, name, threshold)` → `list[(InventoryItem, score)]` | GIN trigram search |
| `confirm_invoice` | `(staging_id, restaurant_id, resolutions)` → None | Batch confirm with resolution map |

`record_transaction` implementation:
```python
# All in one db.transaction() / same session
txn = InventoryTransaction(...)
db.add(txn)
db.flush()  # get txn.id

delta = quantity if txn_type == 'credit' else -quantity
balance = db.scalar(
    select(InventoryBalance)
    .where(...)
    .with_for_update()
)
if balance:
    balance.balance += delta
    balance.last_txn_id = txn.id
    balance.updated_at = now()
else:
    db.add(InventoryBalance(balance=delta, last_txn_id=txn.id, ...))

# caller owns commit
```

---

### Ticket I-5: Wire `/inventory` and `/balance` to real data

**File:** `app/telegram/handlers/commands.py`

Replace stubs with real queries via `InventoryService`.

`/inventory`:
- `list_items(restaurant_id, offset=0, limit=10)`
- Format: `{name} — {balance} {unit}`
- Negative balance → ⚠️ prefix
- Pagination via `set_list_state` + inline keyboard

`/balance`:
- `get_balance_summary(restaurant_id)`
- Format: total items, zero-stock count (⚠️ if > 0), negative count (⚠️ if > 0)

---

### Ticket I-6: Tests

**File:** `tests/test_inventory_service.py`

- `record_transaction` credit: balance created, correct value
- `record_transaction` debit: balance decremented
- Two credits + one debit: correct net balance
- Negative balance permitted
- `fuzzy_match_item`: above/below threshold filtering
- `get_balance_summary`: counts correct

**File:** `tests/test_commands.py` (additions)
- `/inventory` with real data
- `/balance` summary format

**Run:** `uv run pytest tests/test_inventory_service.py tests/test_commands.py -v`

---

### Ticket I-7: Commit + merge

```bash
git commit -m "feat(step-7): inventory data model + service + wired read commands"
# merge phase-1-step-7 → develop, delete branch
```

---

## Phase 3 — Upload Pipeline (branch: `phase-1-step-8`)

### Goal
File upload → doc type classification → OCR/extraction → staging review → confirm → DB write.

---

### Ticket U-1: `StagingService`

**File:** `app/services/staging_service.py`

Methods:
- `create(uploaded_by, restaurant_id, file_id, file_kind, mime, filename, size)` → staging record
- `set_document_type(staging_id, document_type)` → update + return record
- `set_extracted_data(staging_id, data)` → update JSONB + set status=pending_review
- `set_status(staging_id, status)` → update status
- `get(staging_id)` → staging record
- `list_pending(restaurant_id)` → staging records in pending_review

---

### Ticket U-2: `files.py` handler

**File:** `app/telegram/handlers/files.py`

On file message received:
1. Extract file metadata from update (file_id, file_unique_id, mime, size, filename/caption)
2. Require active restaurant
3. `StagingService.create(...)` → staging record
4. Store `staging_id` in `user.context["active_staging_id"]`
5. `db.commit()`
6. Send inline keyboard:
   ```
   What is this document?
   [1️⃣ Invoice]  [2️⃣ Price List]
   ```

---

### Ticket U-3: Button handlers for doc type selection

**File:** `app/telegram/handlers/buttons.py`

Callbacks `doc_invoice` and `doc_pricelist`:
1. Get `active_staging_id` from context
2. `StagingService.set_document_type(staging_id, document_type)`
3. `db.commit()`
4. Dispatch Celery task: `process_file_task.delay(staging_id, document_type)`
5. Send: "Got it! Processing your {invoice/price list}... I'll notify you when done."

---

### Ticket U-4: `ocr_tasks.py` — Celery extraction worker

**File:** `app/workers/ocr_tasks.py`

`process_file_task(staging_id, document_type)`:
1. Load staging record, download file from Telegram
2. Detect file type (image vs PDF by mime)
3. Image path: Gemini Flash vision API → raw text
4. PDF path: camelot + pdfplumber → structured text → vision fallback if items < 5
5. Pass raw output to `llm/parser.py` with document_type
6. `StagingService.set_extracted_data(staging_id, parsed_data)`
7. Send Telegram message to user: review message + conf_u / ed_row / del_u keyboard

---

### Ticket U-5: `llm/parser.py` — type-aware structured extraction

**File:** `app/llm/parser.py`

`parse_document(raw_text_or_image, document_type)` → dict matching `extracted_data` schema

Two prompts:
- **Invoice prompt:** Extract supplier name, invoice date, invoice number, currency, and line items (name, qty, unit, unit_price, amount). Return JSON.
- **Price list prompt:** Extract supplier name, effective date, currency, and line items (name, unit, unit_price). Return JSON.

Validate output against expected schema. Return structured dict or raise `ParseError`.

---

### Ticket U-6: Review + edit handlers

**File:** `app/telegram/handlers/buttons.py`

`conf_u` callback:
1. Load staging record + `extracted_data`
2. Fuzzy-match each line item → `inventory_items` (threshold 0.8)
   - If all matched: proceed to confirm
   - If unmatched: show new-item review screen (store resolutions in context)
3. On full resolution: call confirm service (see U-7)

`ed_row` callback: TODO (step 12 in original plan — patch flow)

`del_u` callback: `StagingService.set_status(staging_id, 'cancelled')`, clear context, show menu

---

### Ticket U-7: Confirm services

**File:** `app/services/inventory_service.py` — `confirm_invoice()`

```python
def confirm_invoice(staging_id, restaurant_id, resolutions: dict[str, UUID | None]):
    # resolutions: {line_item_name → inventory_item_id or None (skip)}
    staging = StagingService(db).get(staging_id)
    for item in staging.extracted_data["line_items"]:
        item_id = resolutions.get(item["name"])
        if item_id is None:
            continue  # skipped by user
        self.record_transaction(
            restaurant_id=restaurant_id,
            item_id=item_id,
            txn_type='credit',
            quantity=item["qty"],
            unit_price=item.get("unit_price"),
            amount=item.get("amount"),
            source='invoice',
            staging_id=staging_id,
            created_by=user_id,
        )
    StagingService(db).set_status(staging_id, 'confirmed')
    # caller commits
```

**File:** `app/services/price_service.py` — `confirm_price_list()`

Upsert `supplier_prices` rows. Update `supplier.default_currency` if missing.

---

### Ticket U-8: Wire `/uploads` command

**File:** `app/telegram/handlers/commands.py`

Replace stub with real query:
- `StagingService.list_pending(restaurant_id)`
- Show staging records in `pending_review` status with file type + uploaded date
- `set_numbered_items()` with staging IDs

---

### Ticket U-9: Tests

**Files:**
- `tests/test_staging_service.py` — CRUD methods
- `tests/test_parser.py` — invoice and price list prompts (mocked LLM)
- `tests/test_files_handler.py` — file received → staging created → keyboard sent
- `tests/test_upload_confirm.py` — full invoice confirm flow (mocked OCR + real DB)
  - All items matched: confirm proceeds directly
  - Unmatched items: review screen shown, resolutions stored in context
  - After resolution: transactions + balances created correctly
  - Price list confirm: supplier_prices upserted

**Run:** `uv run pytest tests/ -v`

---

### Ticket U-10: Commit + merge

```bash
git commit -m "feat(step-8): upload pipeline — classify, OCR, staging, confirm"
# merge phase-1-step-8 → develop, delete branch
```

---

## QA Acceptance Criteria Summary

### Step 6 — Reads

| Command | Pass Criteria |
|---------|--------------|
| `/profile` | Shows correct name, restaurant, role |
| `/team` | Shows all active members; "No pending invites" stub |
| `/list suppliers` | Paginated, correct supplier names |
| `/outlets` | All restaurants listed; active marked ✅ |
| `/products` | Items grouped by supplier with price + unit |
| `/prices ABC` | Items for matched supplier with price + date |
| `/inventory` | Stub message until step 7 |
| `/balance` | Stub message until step 7 |

### Step 7 — Inventory Model

| Scenario | Pass Criteria |
|----------|--------------|
| Upload invoice, confirm | `inventory_transactions` rows created, `inventory_balances` updated |
| Same item credited twice | Balance accumulates correctly |
| Debit exceeds credits | Negative balance stored; `/balance` shows ⚠️ |
| `/inventory` | Items listed with live balance |
| `/balance` | Correct totals, zero/negative flags |

### Step 8 — Upload Pipeline

| Scenario | Pass Criteria |
|----------|--------------|
| Upload image | Bot asks invoice vs price list |
| Upload PDF | Bot asks invoice vs price list |
| Select invoice | Celery runs, extracted line items shown for review |
| Select price list | Celery runs, extracted prices shown for review |
| Confirm invoice (all known items) | Transactions + balances created |
| Confirm invoice (new items) | Review screen shown, resolutions stored, confirm after |
| Confirm price list | `supplier_prices` upserted |
| OCR fails | staging.status = error, user notified |
| Currency missing | Prompts user once, saves to supplier |

---

## Backend Dev Tickets — Priority Order

1. R-1 through R-9 (reads, one PR)
2. I-1 through I-3 (models + migration, one PR — QA verifies with Alembic upgrade)
3. I-4 through I-6 (service + wired commands, one PR)
4. U-1 through U-4 (staging service + file handler + Celery worker, one PR)
5. U-5 through U-9 (parser + confirm + tests, one PR)
