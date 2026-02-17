# Phase 1 Technical Specification
## Supplier & Price List Management

---

## 1. Scope

Phase 1 delivers two things via Telegram chat:
1. **Supplier management** — create, list, view suppliers per restaurant.
2. **Price list ingestion** — upload a PDF or photo, review parsed items via inline buttons, edit via text, confirm to write to DB.

No web UI. Every interaction is Telegram-native.

---

## 2. Database Schema

### 2a. Amend: `restaurants`
Add location fields to support multi-outlet identification.

```sql
ALTER TABLE restaurants
  ADD COLUMN address   TEXT,
  ADD COLUMN city      TEXT,
  ADD COLUMN country   CHAR(2),       -- ISO 3166-1 alpha-2
  ADD COLUMN latitude  NUMERIC(10,7), -- nullable
  ADD COLUMN longitude NUMERIC(10,7); -- nullable
```

### 2b. New: `suppliers`

```sql
CREATE TABLE suppliers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    name_lower      TEXT NOT NULL,   -- always lowercased; used for trigram/fuzzy search
    contact_name    TEXT,
    phone           TEXT,
    currency        CHAR(3),         -- e.g. "SGD", "USD"
    notes           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_suppliers_restaurant_name ON suppliers(restaurant_id, name_lower);
CREATE INDEX idx_suppliers_name_trgm ON suppliers USING GIN(name_lower gin_trgm_ops);
```

### 2c. New: `supplier_price_lists`
Header record for each confirmed upload.

```sql
CREATE TABLE supplier_price_lists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    supplier_id     UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    effective_date  DATE,            -- nullable; supplier may not state it
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2d. New: `supplier_prices`
Append-only. One row per item per upload. Full history preserved.

```sql
CREATE TABLE supplier_prices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    price_list_id   UUID NOT NULL REFERENCES supplier_price_lists(id) ON DELETE CASCADE,
    supplier_id     UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    item_name       TEXT NOT NULL,         -- raw name as written by supplier
    item_name_lower TEXT NOT NULL,         -- lowercased for search
    unit            TEXT,                  -- "kg", "case", "each", "ltr"
    unit_qty        NUMERIC(10,3),         -- e.g. 10.0 if "case of 10kg"
    price           NUMERIC(12,4) NOT NULL,
    currency        CHAR(3),
    sku             TEXT,                  -- supplier's own code, nullable
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_supplier_prices_list ON supplier_prices(price_list_id);
CREATE INDEX idx_supplier_prices_supplier ON supplier_prices(supplier_id, item_name_lower);
CREATE INDEX idx_supplier_prices_name_trgm ON supplier_prices USING GIN(item_name_lower gin_trgm_ops);
```

### 2e. New: `file_processing_staging`
Working state for an upload in progress. Mutated until user confirms.

```sql
CREATE TYPE staging_status AS ENUM ('processing', 'pending_review', 'confirmed', 'cancelled');

CREATE TABLE file_processing_staging (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id        UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    supplier_id          UUID REFERENCES suppliers(id) ON DELETE SET NULL, -- nullable until resolved
    file_id              TEXT NOT NULL,   -- Telegram file_id for re-download
    file_unique_id       TEXT NOT NULL,
    mime                 TEXT,
    status               staging_status NOT NULL DEFAULT 'processing',
    extracted_data_json  JSONB,           -- working copy; patched during review
    error_message        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_staging_restaurant_status ON file_processing_staging(restaurant_id, status);
```

**`extracted_data_json` shape:**
```json
{
  "supplier_name": "ABC Wholesalers",
  "effective_date": "2025-01-15",
  "currency": "SGD",
  "items": [
    {
      "item_name": "tomato",
      "sku": "TOM-001",
      "unit": "kg",
      "unit_qty": 1,
      "price": 2.50
    }
  ]
}
```

### 2f. New: `handshake_requests`
Pending clarification questions to the user.

```sql
CREATE TABLE handshake_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staging_id      UUID NOT NULL REFERENCES file_processing_staging(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,   -- "confirm_supplier" | "confirm_unit" | "confirm_currency"
    question_text   TEXT NOT NULL,
    context_data    JSONB,           -- handler reads this to act on the answer
    answer          TEXT,            -- set on resolution
    resolved_at     TIMESTAMPTZ,     -- null = still open
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_handshake_staging ON handshake_requests(staging_id, resolved_at);
CREATE INDEX idx_handshake_user ON handshake_requests(user_id, resolved_at);
```

### 2g. Transient Context: `users.context` (JSONB)
Already exists. Schema for Phase 1:

```json
{
  "active_restaurant_id": "uuid",
  "last_list_type": "suppliers",
  "last_list_offset": 20,
  "numbered_items": ["uuid-a", "uuid-b", "uuid-c"],
  "active_staging_id": "uuid"
}
```

---

## 3. Message Router

Every incoming message passes through the router in priority order. First match wins.

```
app/telegram/router.py
```

```
Priority 1 — Global Reset
Priority 2 — Button Callback (callback_query)
Priority 3 — Slash Command
Priority 4 — File Upload
Priority 5 — Structured Pattern (regex)
Priority 6 — LLM Fallback
```

### Priority 1: Global Reset
**Matches:** text is one of `home`, `menu`, `cancel`, `exit`, `/start` (case-insensitive).

**Action:**
- Clear `user.context` fields: `last_list_type`, `last_list_offset`, `numbered_items`, `active_staging_id`
- Keep `active_restaurant_id`
- Reply with Main Menu

**Main Menu message:**
```
👋 What would you like to do?

/list suppliers
/add supplier
/uploads
```

---

### Priority 2: Button Callback

**Wire format:** `action:id:param` (Telegram 64-byte limit)

| Action | Format | Handler |
|--------|--------|---------|
| `rev_u` | `rev_u:{staging_hex}` | Show upload review |
| `conf_u` | `conf_u:{staging_hex}` | Confirm upload → write to DB |
| `del_u` | `del_u:{staging_hex}` | Cancel upload |
| `ed_row` | `ed_row:{staging_hex}:{idx}` | Prompt edit for item at index |
| `list_p` | `list_p:{type}:{page}` | Paginate a list |
| `res_h` | `res_h:{handshake_hex}:{answer}` | Resolve handshake question |
| `set_sup` | `set_sup:{supplier_hex}` | Set active supplier on staging |

**Rules:**
- All button handlers are **strictly deterministic**. No LLM calls.
- If a referenced ID no longer exists → answer callback with alert: `"This item no longer exists."`
- After state-changing actions (`conf_u`, `del_u`), **edit the original message** to show final state. Prevents double-tap.

---

### Priority 3: Slash Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/list suppliers` | `cmd_list_suppliers` | Paginated supplier list |
| `/add supplier` | `cmd_add_supplier` | Start supplier creation |
| `/uploads` | `cmd_list_uploads` | Show pending staging records |
| `/switch` | `cmd_switch_restaurant` | Change active restaurant |

---

### Priority 4: File Upload

**Matches:** update contains `document` or `photo`.

**Flow:**
1. Query `file_processing_staging` for `restaurant_id + status='pending_review'`.
2. If any exist: reply with list of pending uploads + offer to review or start new.
3. If none: download file → enqueue `ocr_task` → reply "Processing your file..."

---

### Priority 5: Structured Pattern (Regex)

| Pattern | Example | Action |
|---------|---------|--------|
| `^#\d+$` | `#2` | Select item `numbered_items[2-1]` from context |
| `^\d+(\.\d+)?\s*(kg|g|ltr|ml|case|each|pcs)` | `5.5 kg` | Quantity+unit input for active flow |

---

### Priority 6: LLM Fallback

**Only reached if no deterministic match.**

1. Build context: `user.context` + last 10 messages + open handshake requests + pending staging.
2. Call LLM Intent Classifier (see Section 5).
3. Validate output against schema.
4. Route to appropriate handler based on `intent`.
5. If intent is `unknown` or confidence < 0.6: reply with disambiguation prompt.

---

## 4. Feature Functions

### 4a. Supplier Management

#### `cmd_add_supplier(db, user, restaurant_id, args)`
- If `args` contains a name: create immediately, confirm.
- If no args: reply asking for supplier name, set `user.context.pending_action = "add_supplier"`.
- On name received: normalize to lowercase, check for duplicate via trigram similarity > 0.85.
  - If duplicate found: show match, ask "Is this the same supplier? [Yes / No, create new]"
  - If new: create supplier, reply "✅ Supplier added: {name}"

#### `cmd_list_suppliers(db, user, restaurant_id, page=0)`
- Query `suppliers` where `restaurant_id` and `is_active=true`, order by `name_lower`.
- Show 10 per page with numbered list (store UUIDs in `user.context.numbered_items`).
- Buttons: `[ Next → ]` (`list_p:suppliers:{page+1}`), `[ ← Prev ]` if page > 0.
- Set `user.context.last_list_type = "suppliers"`, `last_list_offset = page * 10`.

#### `btn_set_supplier(db, staging_id, supplier_id)`
- Update `file_processing_staging.supplier_id`.
- Re-render upload review.

---

### 4b. File Upload & Price List Review

#### `handle_file_upload(db, user, restaurant_id, update)`
1. Extract `file_id`, `file_unique_id`, `mime` from update.
2. Check for existing `pending_review` staging records.
   - If found: show list of pending uploads with `rev_u` buttons + "Start New" option.
   - If none: proceed to step 3.
3. Create `file_processing_staging` record with `status='processing'`.
4. Enqueue `ocr_task(staging_id, file_id, mime)`.
5. Reply: "📄 Got it. Parsing your price list..."

#### `ocr_task(staging_id, file_id, mime)` — Celery worker
1. Download file bytes from Telegram.
2. Extract text:
   - PDF: attempt `pdfplumber` text extraction first.
   - Image or failed PDF: call Vision LLM (see Section 5, Contract B).
3. Call Price List Parser LLM (see Section 5, Contract B) with extracted text.
4. Validate output schema.
5. Update staging: `extracted_data_json = parsed_result`, `status = 'pending_review'`.
6. If supplier name in result: run fuzzy match against `suppliers`.
   - Strong match (>0.85): auto-set `staging.supplier_id`.
   - Weak match: create `handshake_request` of type `confirm_supplier`.
   - No match: create `handshake_request` to ask user if they want to create a new supplier.
7. Send message to user with upload review (call `render_upload_review`).

#### `render_upload_review(staging)` → Telegram message
```
📄 Price List: {supplier_name or "Unknown Supplier"}
Date: {effective_date or "Not found"}
Currency: {currency}
Items: {count}

1. Tomato — 2.50 SGD/kg
2. Onion — 1.80 SGD/kg
3. Garlic — 5.00 SGD/kg
...

[ ✅ Confirm All ]   → conf_u:{id}
[ ❌ Cancel ]        → del_u:{id}
[ ✏️ Edit Item #1 ]  → ed_row:{id}:1
[ ✏️ Edit Item #2 ]  → ed_row:{id}:2
```

#### `btn_confirm_upload(db, staging_id)` — `conf_u` handler
1. Load staging record. Validate `status = 'pending_review'`.
2. Require `supplier_id` to be set — if not, prompt user to identify supplier first.
3. Create `supplier_price_lists` record.
4. Bulk insert `supplier_prices` rows from `extracted_data_json.items`.
   - Store `item_name` raw + `item_name_lower = item_name.lower()`.
5. Set `staging.status = 'confirmed'`.
6. Clear `user.context.active_staging_id`.
7. Edit original message: "✅ Confirmed. {n} prices saved for {supplier_name}."

#### `btn_edit_row(db, staging_id, idx)` — `ed_row` handler
1. Load item at `extracted_data_json.items[idx-1]`.
2. Set `user.context.active_staging_id`, store `edit_idx` in context.
3. Reply: "Editing: {item_name} — current price {price} {currency}/{unit}\n\nSend the corrected line, e.g.:\n`tomato 2.80 sgd/kg`"

#### `handle_staging_text_edit(db, user, staging_id, edit_idx, text)`
Called when user sends text while `active_staging_id` is set and no other pattern matches.
1. Call Correction Patch LLM (see Section 5, Contract C).
2. Validate patch — only `replace` operations on `items[n].*`, no structural changes.
3. Apply patch to `extracted_data_json`.
4. Save staging record.
5. Re-render upload review.

#### `btn_delete_upload(db, staging_id)` — `del_u` handler
1. Set `staging.status = 'cancelled'`.
2. Clear `user.context.active_staging_id`.
3. Edit original message: "❌ Upload cancelled."

---

### 4c. Handshake Resolution

#### `btn_resolve_handshake(db, handshake_id, answer)` — `res_h` handler
1. Load handshake record. Load `context_data`.
2. Based on `type`:
   - `confirm_supplier` + `yes`: set `staging.supplier_id = context_data.matched_supplier_id`.
   - `confirm_supplier` + `no`: prompt user to type supplier name → create new supplier.
   - `confirm_unit`: set `items[idx].unit = context_data.suggested_unit` if `yes`.
   - `confirm_currency`: set `extracted_data_json.currency` if `yes`.
3. Set `handshake.answer = answer`, `handshake.resolved_at = now()`.
4. Re-render upload review.

---

### 4d. Restaurant Context

#### `cmd_switch_restaurant(db, user)`
- List restaurants user is active member of.
- Numbered list stored in `user.context.numbered_items`.
- User replies `#1` → `active_restaurant_id` updated in `user.context`.

**Context guard:** Every handler that touches restaurant data must check `user.context.active_restaurant_id`. If not set and user has exactly one restaurant → auto-set. If multiple and none active → prompt `/switch`.

---

## 5. LLM Contracts

### Contract A — Intent Classifier
**Purpose:** Resolve free-form text to an intent when no deterministic route matches.
**Model:** Cheap, fast (e.g. `gpt-4o-mini`).
**Called by:** Priority 6 (LLM fallback router).

**System prompt skeleton:**
```
You are a routing assistant for a restaurant operations Telegram bot.
Classify the user message into one of the allowed intents.
Return only valid JSON. Do not explain.
```

**Input:**
```json
{
  "message": "user message text",
  "context": {
    "active_restaurant": "name",
    "active_staging_id": "uuid or null",
    "pending_handshakes": 2,
    "recent_messages": ["...", "..."]
  }
}
```

**Output schema (strict):**
```json
{
  "intent": "add_supplier | list_suppliers | edit_staging | resolve_upload | unknown",
  "entities": {
    "supplier_name": "string or null",
    "item_name": "string or null",
    "price": "number or null",
    "unit": "string or null",
    "currency": "string or null"
  },
  "confidence": 0.0
}
```

**Validation rules:**
- `intent` must be one of the defined enum values.
- `confidence` must be a float 0.0–1.0.
- If confidence < 0.6: treat as `unknown`, send disambiguation.
- Any extra keys in output: reject, treat as `unknown`.

---

### Contract B — Price List Parser
**Purpose:** Convert raw extracted text (from PDF or image) into structured JSON.
**Model:** Mid-tier, good at structured extraction (e.g. `gpt-4o`).
**Called by:** `ocr_task` Celery worker.

**System prompt skeleton:**
```
You are a data extraction assistant for supplier price lists.
Extract all products and prices from the provided text.
Return only valid JSON matching the schema exactly. Do not explain.
If a field cannot be found, set it to null.
```

**Input:**
```json
{
  "text": "raw extracted text from PDF or image"
}
```

**Output schema (strict):**
```json
{
  "supplier_name": "string or null",
  "effective_date": "YYYY-MM-DD or null",
  "currency": "SGD or null",
  "items": [
    {
      "item_name": "string",
      "sku": "string or null",
      "unit": "string or null",
      "unit_qty": "number or null",
      "price": "number"
    }
  ]
}
```

**Validation rules:**
- `items` must be a non-empty array.
- Each item must have `item_name` (non-empty string) and `price` (positive number).
- `effective_date` must parse as ISO date or be null.
- `currency` must be 3-character string or null.
- If validation fails: set `staging.status = 'error'`, notify user.

---

### Contract C — Correction Patch Generator
**Purpose:** Convert a user's freeform correction into a RFC 6902 JSONPatch.
**Model:** Cheap, fast (e.g. `gpt-4o-mini`).
**Called by:** `handle_staging_text_edit`.

**System prompt skeleton:**
```
You are a JSON patch generator for a price list editor.
Given the current item and the user's correction, return a JSONPatch array.
Only generate 'replace' operations on existing fields.
Return only valid JSON. Do not explain.
```

**Input:**
```json
{
  "item_index": 2,
  "current_item": {
    "item_name": "tomato",
    "unit": "kg",
    "price": 2.50,
    "currency": "SGD"
  },
  "user_correction": "change price to 2.80"
}
```

**Output schema (strict):**
```json
[
  {"op": "replace", "path": "/items/2/price", "value": 2.80}
]
```

**Validation rules (applied before patch is executed):**
- Only `replace` operation allowed. Reject `add`, `remove`, `move`, `copy`.
- Path must match pattern `/items/{n}/{field}` where `n` is an integer and `field` is one of: `item_name`, `unit`, `unit_qty`, `price`, `currency`, `sku`.
- Value types must match field: `price` and `unit_qty` must be positive numbers.
- Reject any patch that changes `item_index` out of bounds.

---

## 6. File & Folder Structure

```
app/
├── core/
│   ├── config.py
│   └── logging.py
│
├── db/
│   ├── base.py
│   ├── session.py
│   └── models/
│       ├── user.py
│       ├── restaurant.py
│       ├── restaurant_user.py
│       ├── telegram_session.py
│       ├── telegram_messages.py
│       ├── telegram_outgoing_messages.py
│       ├── llm_calls.py
│       ├── suppliers.py               ← NEW
│       ├── supplier_price_lists.py    ← NEW
│       ├── supplier_prices.py         ← NEW
│       ├── file_processing_staging.py ← NEW
│       └── handshake_requests.py      ← NEW
│
├── services/
│   ├── user_service.py
│   ├── restaurant_service.py
│   ├── supplier_service.py            ← NEW
│   ├── staging_service.py             ← NEW  (state machine for uploads)
│   ├── price_service.py               ← NEW  (confirm staging → write prices)
│   ├── context_service.py             ← NEW  (read/write user.context JSONB)
│   ├── entity_resolver.py
│   └── telemetry.py
│
├── llm/                               ← NEW (LLM contracts)
│   ├── intent.py                      ← Contract A
│   ├── parser.py                      ← Contract B
│   └── patcher.py                     ← Contract C
│
├── schemas/
│   ├── user.py
│   ├── restaurant.py
│   ├── supplier.py                    ← NEW
│   └── staging.py                     ← NEW
│
├── api/
│   └── v1/routes/
│       ├── auth.py
│       ├── restaurants.py
│       └── telegram.py
│
├── telegram/
│   ├── router.py                      ← NEW (6-priority router)
│   ├── handlers/                      ← NEW
│   │   ├── reset.py                   ← Priority 1
│   │   ├── buttons.py                 ← Priority 2
│   │   ├── commands.py                ← Priority 3
│   │   ├── files.py                   ← Priority 4
│   │   ├── patterns.py                ← Priority 5
│   │   └── llm_fallback.py            ← Priority 6
│   ├── keyboards.py                   ← NEW (inline keyboard builders)
│   ├── renderer.py                    ← NEW (message formatters)
│   ├── bot_api.py
│   ├── ack_handler.py
│   ├── ingest.py
│   └── commands.py
│
└── workers/
    ├── celery_app.py
    ├── telegram_tasks.py              ← calls router.py
    ├── ocr_tasks.py                   ← NEW (download + parse + stage)
    ├── celery_types.py
    ├── db.py
    └── utils.py
```

---

## 7. Build Order

Build in this sequence to avoid blocking dependencies:

1. **DB models + migration** — all 5 new tables + restaurant location columns.
2. **`context_service.py`** — `get_context()`, `set_context()`, `clear_context()` helpers. Everything else depends on this.
3. **`supplier_service.py`** — create, list, fuzzy match.
4. **`router.py`** — skeleton with all 6 priorities, each dispatching to a handler stub.
5. **`handlers/reset.py`** — simplest handler, gets routing working end-to-end.
6. **`handlers/commands.py`** — `/list suppliers`, `/add supplier`, `/uploads`, `/switch`.
7. **`keyboards.py` + `renderer.py`** — shared formatting used by all handlers.
8. **`handlers/buttons.py`** — `rev_u`, `del_u`, `set_sup`, `list_p`.
9. **`staging_service.py`** + **`handlers/files.py`** — file upload flow without OCR (stub the parse step).
10. **`llm/parser.py`** + **`workers/ocr_tasks.py`** — plug in real parsing.
11. **`llm/patcher.py`** + **`handlers/buttons.py`** `ed_row` — edit flow.
12. **`btn_confirm_upload`** + **`price_service.py`** — final write to `supplier_price_lists` + `supplier_prices`.
13. **`handlers/llm_fallback.py`** + **`llm/intent.py`** — LLM router last (everything else must be solid first).

---

## 8. Error Handling & Safety

| Scenario | Handling |
|----------|----------|
| OCR/parse fails | `staging.status = 'error'`, message user with "Couldn't parse that file. Try a clearer photo or PDF." |
| LLM output fails schema validation | Log, treat as `unknown` intent, send disambiguation |
| Button references deleted ID | Answer callback_query with alert text, no crash |
| Confirm without supplier set | Block: "Please identify the supplier first." with button list |
| Patch out of bounds | Reject patch, reply "I couldn't apply that edit. Please try again." |
| No active restaurant | Prompt `/switch` before any restaurant-scoped action |
| Duplicate supplier name (similarity > 0.85) | Prompt: "Did you mean {existing}? [Yes / No, create new]" |

---

## 9. Not in Phase 1

- Purchase orders
- Inventory tracking
- Multi-outlet / brand grouping
- Analytics or reporting
- Any API routes for supplier/price data (Telegram only)
- Price comparison across suppliers
