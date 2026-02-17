# Phase 1 Technical Specification
## Supplier & Price List Management

**Revision 3** — corrected: integer money storage, global supplier registry, patch flow, auth on staging, pg_trgm declaration, user confirmation gates, /link command.

---

## 1. Scope

Phase 1 delivers two things via Telegram chat:
1. **Supplier management** — create, list, view suppliers; link them to restaurants.
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
  ADD COLUMN latitude  NUMERIC(10,7), -- nullable; no PostGIS dependency yet
  ADD COLUMN longitude NUMERIC(10,7); -- nullable
```

---

### 2b. Migration Prerequisites
Before any table or index creation:

```sql
-- Must run first — required for all GIN trigram indexes
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

---

### 2c. New: `suppliers` — Global Registry
Suppliers are a global entity. One `ABC Wholesalers` record exists once in the system,
linkable to many restaurants/outlets via `restaurant_suppliers`.

```sql
CREATE TABLE suppliers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    name_lower      TEXT NOT NULL,    -- always lowercased; used for trigram/fuzzy search
    contact_name    TEXT,
    phone           TEXT,
    email           TEXT,
    default_currency CHAR(3),         -- e.g. "SGD", "USD" — hint for price list parsing
    notes           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_suppliers_name_lower ON suppliers(name_lower);
CREATE INDEX idx_suppliers_name_trgm ON suppliers USING GIN(name_lower gin_trgm_ops);
```

---

### 2d. New: `restaurant_suppliers` — Link Table
Connects a restaurant to the global suppliers it works with.

```sql
CREATE TABLE restaurant_suppliers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    supplier_id     UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT,             -- restaurant-specific notes about this supplier
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_by        UUID REFERENCES users(id) ON DELETE SET NULL,

    UNIQUE(restaurant_id, supplier_id)
);

CREATE INDEX idx_restaurant_suppliers_restaurant ON restaurant_suppliers(restaurant_id, is_active);
```

---

### 2e. New: `supplier_price_lists`
Header record for each confirmed upload. Scoped to the restaurant that uploaded it.

```sql
CREATE TABLE supplier_price_lists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    supplier_id     UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    effective_date  DATE,             -- nullable; supplier may not state it
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_price_lists_restaurant_supplier ON supplier_price_lists(restaurant_id, supplier_id);
```

---

### 2f. New: `supplier_prices` — Append-only, Integer Storage
One row per item per upload. Full history preserved. **No floats.**

Prices and quantities are stored as integers in minor units with an explicit exponent.

| Field | Example | Meaning |
|-------|---------|---------|
| `price_minor = 250` | `price_exp = 2` | 2.50 (divide by 10²) |
| `price_minor = 500` | `price_exp = 0` | 500 (JPY, no decimal) |
| `unit_qty_minor = 1500` | `unit_qty_exp = 3` | 1.500 kg (divide by 10³) |

```sql
CREATE TABLE supplier_prices (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    price_list_id    UUID NOT NULL REFERENCES supplier_price_lists(id) ON DELETE CASCADE,
    supplier_id      UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    item_name        TEXT NOT NULL,         -- raw name as written by supplier
    item_name_lower  TEXT NOT NULL,         -- lowercased for search
    unit             TEXT,                  -- "kg", "case", "each", "ltr"
    unit_qty_minor   BIGINT,                -- quantity in minor units (nullable)
    unit_qty_exp     SMALLINT,              -- exponent: divide unit_qty_minor by 10^exp
    price_minor      BIGINT NOT NULL,       -- price in minor units
    price_exp        SMALLINT NOT NULL,     -- exponent: divide price_minor by 10^exp
    currency         CHAR(3),
    sku              TEXT,                  -- supplier's own code, nullable
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_supplier_prices_list     ON supplier_prices(price_list_id);
CREATE INDEX idx_supplier_prices_supplier ON supplier_prices(supplier_id, item_name_lower);
CREATE INDEX idx_supplier_prices_name_trgm ON supplier_prices USING GIN(item_name_lower gin_trgm_ops);
```

---

### 2g. New: `file_processing_staging`
Working state for an upload in progress. Mutated until user confirms.

**Single canonical failure state: `'error'`** — no sub-states. Detail goes in `error_message`.

```sql
CREATE TYPE staging_status AS ENUM ('processing', 'pending_review', 'confirmed', 'cancelled', 'error');

CREATE TABLE file_processing_staging (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id        UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    uploaded_by          UUID NOT NULL REFERENCES users(id),               -- mandatory auth anchor
    session_id           UUID REFERENCES telegram_sessions(id) ON DELETE SET NULL, -- nullable; session may expire
    supplier_id          UUID REFERENCES suppliers(id) ON DELETE SET NULL, -- nullable until resolved
    file_id              TEXT NOT NULL,   -- Telegram file_id for re-download
    file_unique_id       TEXT NOT NULL,
    mime                 TEXT,
    status               staging_status NOT NULL DEFAULT 'processing',
    extracted_data_json  JSONB,           -- working copy; patched during review (integer prices)
    error_message        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_staging_restaurant_status ON file_processing_staging(restaurant_id, status);
CREATE INDEX idx_staging_uploaded_by       ON file_processing_staging(uploaded_by);
```

**Auth rule:** Only `uploaded_by` user or restaurant `is_owner` member may confirm, edit, or cancel a staging record. Enforce in every button handler before touching staging.

**`extracted_data_json` shape — integer prices throughout:**
```json
{
  "supplier_name": "ABC Wholesalers",
  "effective_date": "2025-01-15",
  "currency": "SGD",
  "currency_exp": 2,
  "items": [
    {
      "item_name": "Tomato",
      "item_name_lower": "tomato",
      "sku": "TOM-001",
      "unit": "kg",
      "unit_qty_minor": 1000,
      "unit_qty_exp": 3,
      "price_minor": 250,
      "price_exp": 2
    }
  ]
}
```

**Display helper** (used by `renderer.py` — never stored):
```
price_display = price_minor / 10^price_exp   → "2.50"
unit_qty_display = unit_qty_minor / 10^unit_qty_exp → "1.000 kg"
```

---

### 2h. New: `handshake_requests`
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
CREATE INDEX idx_handshake_user    ON handshake_requests(user_id, resolved_at);
```

---

### 2i. Transient Context: `users.context` (JSONB)
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

**Wire format:** `action:id:param` (Telegram 64-byte limit; UUID as 32-char hex, no dashes)

| Action | Format | Handler |
|--------|--------|---------|
| `rev_u` | `rev_u:{staging_hex}` | Show upload review |
| `conf_u` | `conf_u:{staging_hex}` | Confirm upload → write to DB |
| `del_u` | `del_u:{staging_hex}` | Cancel upload |
| `ed_row` | `ed_row:{staging_hex}:{idx}` | Prompt edit for item at index |
| `list_p` | `list_p:{type}:{page}` | Paginate a list (type = "suppliers", "uploads") |
| `res_h` | `res_h:{handshake_hex}:{answer}` | Resolve handshake question |
| `set_sup` | `set_sup:{staging_hex}:{supplier_hex}` | Link supplier to staging record |
| `new_sup` | `new_sup:{staging_hex}` | Create new supplier from staging's parsed name |

**Rules:**
- All button handlers are **strictly deterministic**. No LLM calls.
- If a referenced ID no longer exists → answer callback with alert: `"This item no longer exists."`
- After state-changing actions (`conf_u`, `del_u`), **edit the original message** to reflect final state. Prevents double-tap.

---

### Priority 3: Slash Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/list suppliers` | `cmd_list_suppliers` | Paginated list of this restaurant's linked suppliers |
| `/add supplier` | `cmd_add_supplier` | Search global registry + create new if not found |
| `/link supplier` | `cmd_link_supplier` | Search global registry + link existing supplier to restaurant |
| `/uploads` | `cmd_list_uploads` | Show pending staging records |
| `/switch` | `cmd_switch_restaurant` | Change active restaurant |

---

### Priority 4: File Upload

**Matches:** update contains `document` or `photo`.

**Flow:**
1. Query `file_processing_staging` for `restaurant_id + status='pending_review'`.
2. If any exist: reply with list of pending uploads + offer to review or start new.
3. If none: create staging record → enqueue `ocr_task` → reply "Processing your file..."

---

### Priority 5: Structured Pattern (Regex)

| Pattern | Example | Action |
|---------|---------|--------|
| `^#\d+$` | `#2` | Select `numbered_items[2-1]` from context |
| `^\d+(\.\d+)?\s*(kg\|g\|ltr\|ml\|case\|each\|pcs)` | `5.5 kg` | Quantity+unit input for active flow |

---

### Priority 6: LLM Fallback

**Only reached if no deterministic match.**

1. Build context: `user.context` + last 10 messages + open handshake requests + pending staging.
2. Call LLM Intent Classifier (see Section 5, Contract A).
3. Validate output against schema.
4. Route to appropriate handler based on `intent`.
5. If `unknown` or confidence < 0.6: reply with disambiguation prompt.

---

## 4. Feature Functions

### 4a. Supplier Management

#### Global Registry Confirmation Gates
Two gates prevent near-duplicate accumulation. Both require explicit user confirmation.

**Gate 1 — Before any new global record is written:**
Search entire global `suppliers` by trigram similarity > 0.75. If any match exists:
> "Did you mean **{existing name}**? [Yes, that's them / No, create new]"
Only write a new global record if user explicitly says No.

**Gate 2 — Before linking:**
If the match is already linked to this restaurant:
> "You're already connected to {name}."
Stop — no duplicate link created.

---

#### `cmd_add_supplier(db, user, restaurant_id, args)`
Intent: create a new supplier that doesn't exist in the global registry yet.

1. If `args` is empty: ask for supplier name.
2. Normalize to lowercase. Apply Gate 1 (global search, similarity > 0.75).
3. If no match (or user confirms "No, create new"): create supplier in global registry + create `restaurant_suppliers` link in one transaction.
4. Reply "✅ Supplier added: {name}"

#### `cmd_link_supplier(db, user, restaurant_id, args)`
Intent: link an existing global supplier to this restaurant without uploading a price list.

1. If `args` is empty: ask for supplier name.
2. Normalize to lowercase. Search global registry by trigram similarity > 0.75.
3. Show matches as numbered list with `[ ✅ Link ]` buttons (`set_sup:{staging=none}:{supplier_hex}`).
4. Apply Gate 2: if already linked, say so and stop.
5. On link confirmation: create `restaurant_suppliers` row. Reply "✅ Linked: {name}"

#### `cmd_list_suppliers(db, user, restaurant_id, page=0)`
- Query via join: `restaurant_suppliers ↔ suppliers` where `restaurant_id` and `rs.is_active=true`.
- Show 10 per page, ordered by `name_lower`.
- Store UUIDs in `user.context.numbered_items`.
- Buttons: `[ Next → ]` (`list_p:suppliers:{page+1}`), `[ ← Prev ]` if page > 0.
- Set `user.context.last_list_type = "suppliers"`, `last_list_offset = page * 10`.

#### `btn_set_supplier(db, staging_id, supplier_id)`
- Verify supplier is linked to the staging record's `restaurant_id` via `restaurant_suppliers`.
- If not linked: create the link (user already approved via handshake or button).
- Update `file_processing_staging.supplier_id`.
- Re-render upload review.

---

### 4b. File Upload & Price List Review

#### `handle_file_upload(db, user, restaurant_id, update)`
1. Extract `file_id`, `file_unique_id`, `mime` from update.
2. Check for existing `pending_review` staging records for this restaurant.
   - If found: show list with `rev_u` buttons + "Start New" option.
   - If none: proceed.
3. Create `file_processing_staging` record with `status='processing'`, `uploaded_by=user.id`, `session_id` from current session.
4. Enqueue `ocr_task(staging_id, file_id, mime)`.
5. Reply: "📄 Got it. Parsing your price list..."

#### `ocr_task(staging_id, file_id, mime)` — Celery worker
1. Download file bytes from Telegram.
2. Extract text:
   - PDF: attempt `pdfplumber` text extraction first.
   - Image or failed PDF: call Vision LLM.
3. Call Price List Parser LLM (Contract B) → receives **decimal** output.
4. **Conversion boundary:** Convert all decimal prices/quantities to integer minor units before writing to staging.
   - `price 2.50 SGD` → `price_minor=250, price_exp=2, currency="SGD"`
   - `unit_qty 1.5` → `unit_qty_minor=1500, unit_qty_exp=3`
5. Validate converted structure. If invalid: set `staging.status='error'`, notify user.
6. Write `extracted_data_json` (integer format), set `status='pending_review'`.
7. Run fuzzy match on `supplier_name` against global `suppliers`:
   - Strong match (>0.85) AND linked to restaurant: auto-set `staging.supplier_id`.
   - Strong match but NOT linked: create `handshake_request` type `confirm_supplier` (offer to link).
   - Weak/no match: create `handshake_request` to ask user (offer to create new or pick from list).
8. Send upload review message.

#### `render_upload_review(staging)` → Telegram message
Prices displayed in human-readable form (convert from minor units for display only).

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
1. Load staging record. Validate `status='pending_review'`.
2. Require `supplier_id` — if not set, block: "Please identify the supplier first."
3. Verify `restaurant_suppliers` link exists between `staging.restaurant_id` and `staging.supplier_id`. Create link if missing (user already approved via handshake).
4. Create `supplier_price_lists` record.
5. Bulk insert `supplier_prices` rows from `extracted_data_json.items` — all values already in integer format.
6. Set `staging.status='confirmed'`. Clear `user.context.active_staging_id`.
7. Edit original message: "✅ Confirmed. {n} prices saved for {supplier_name}."

#### `btn_edit_row(db, staging_id, idx)` — `ed_row` handler
1. Load item at `extracted_data_json.items[idx-1]`.
2. Convert to display values for the prompt.
3. Store `edit_idx` in `user.context`.
4. Reply: "Editing: {item_name} — current price {display_price} {currency}/{unit}\n\nSend the corrected line:\n`tomato 2.80 sgd/kg`"

#### `handle_staging_text_edit(db, user, staging_id, edit_idx, text)`
Called when text is received while `active_staging_id` is set and no other pattern matched.

**Patch applies to display schema (decimals), not storage schema (integers).**

1. Load item at `extracted_data_json.items[edit_idx]`. Convert to display form (integers → decimals).
2. Send display-form item + user correction to Correction Patch LLM (Contract C).
3. LLM returns patch in decimal form.
4. Validate patch: only `replace` on allowed fields, values are positive numbers, index in bounds.
5. Apply patch to the **display-form item** (not to staging JSON directly).
6. Validate the full patched display-form item.
7. **Convert the entire patched display-form item back to integer format** (decimals → minor units).
8. Write the integer item back to `extracted_data_json.items[edit_idx]`.
9. Save staging record. Re-render upload review.

#### `btn_delete_upload(db, staging_id)` — `del_u` handler
1. Set `staging.status='cancelled'`. Clear `user.context.active_staging_id`.
2. Edit original message: "❌ Upload cancelled."

---

### 4c. Handshake Resolution

#### `btn_resolve_handshake(db, handshake_id, answer)` — `res_h` handler
1. Load handshake record + `context_data`.
2. Based on `type`:
   - `confirm_supplier` + `yes`: set `staging.supplier_id = context_data.matched_supplier_id`. Create `restaurant_suppliers` link if not exists.
   - `confirm_supplier` + `no`: prompt user to type a name → run `cmd_add_supplier` flow.
   - `confirm_unit` + `yes`: update `items[idx].unit` in staging JSON.
   - `confirm_currency` + `yes`: update `extracted_data_json.currency` + recalculate `currency_exp`.
3. Set `handshake.answer = answer`, `handshake.resolved_at = now()`.
4. Re-render upload review.

---

### 4d. Restaurant Context

#### `cmd_switch_restaurant(db, user)`
- List all restaurants the user is an active member of.
- Numbered list stored in `user.context.numbered_items`.
- User replies `#1` → set `user.context.active_restaurant_id`.

**Context guard:** Every handler that touches restaurant-scoped data checks `user.context.active_restaurant_id`. If not set and user has one restaurant → auto-set. If multiple and none active → prompt `/switch`.

---

## 5. LLM Contracts

### Conversion Boundary Rule
LLMs always speak in human-readable decimals. Our code owns the conversion:
- **Inbound (LLM → our code):** Convert decimals to integer minor units immediately after validation.
- **Outbound (our code → LLM):** Convert integer minor units back to decimals before sending.
- **Storage (DB + staging JSON):** Always integers. No floats anywhere at rest.

---

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
    "price": "decimal string or null",
    "unit": "string or null",
    "currency": "string or null"
  },
  "confidence": 0.85
}
```

**Validation rules:**
- `intent` must be one of the defined enum values.
- `confidence` must be a float 0.0–1.0.
- If confidence < 0.6: treat as `unknown`, send disambiguation.
- Any extra keys: reject, treat as `unknown`.

---

### Contract B — Price List Parser
**Purpose:** Convert raw extracted text (from PDF or image) into structured JSON.
**Model:** Mid-tier, good at structured extraction (e.g. `gpt-4o`).
**Called by:** `ocr_task` Celery worker.

**LLM output uses decimals.** Our code converts to integer minor units immediately after validation (see Conversion Boundary Rule).

**System prompt skeleton:**
```
You are a data extraction assistant for supplier price lists.
Extract all products and prices from the provided text.
Return only valid JSON matching the schema exactly. Do not explain.
If a field cannot be found, set it to null.
All prices and quantities must be positive decimal numbers.
```

**Input:**
```json
{
  "text": "raw extracted text from PDF or image"
}
```

**LLM Output schema (decimal — converted by our code before storage):**
```json
{
  "supplier_name": "ABC Wholesalers",
  "effective_date": "2025-01-15",
  "currency": "SGD",
  "items": [
    {
      "item_name": "Tomato",
      "sku": "TOM-001",
      "unit": "kg",
      "unit_qty": 1.0,
      "price": 2.50
    }
  ]
}
```

**Validation rules (before conversion):**
- `items` must be a non-empty array.
- Each item: `item_name` is non-empty string, `price` is positive number.
- `effective_date` must parse as ISO date or be null.
- `currency` must be 3-char string or null.
- If validation fails: `staging.status = 'error'`, notify user.

---

### Contract C — Correction Patch Generator
**Purpose:** Convert a user's freeform correction into an RFC 6902 JSONPatch.
**Model:** Cheap, fast (e.g. `gpt-4o-mini`).
**Called by:** `handle_staging_text_edit`.

**The LLM receives display-form decimals and returns decimal patch values.**
Our code converts the patch values to integer minor units before applying to staging JSON.

**System prompt skeleton:**
```
You are a JSON patch generator for a price list editor.
Given the current item and the user's correction, return a JSONPatch array.
Only generate 'replace' operations on existing fields.
All price/quantity values must be positive decimal numbers.
Return only valid JSON. Do not explain.
```

**Input (display form — converted from integers for the LLM):**
```json
{
  "item_index": 2,
  "current_item": {
    "item_name": "Tomato",
    "unit": "kg",
    "unit_qty": 1.0,
    "price": 2.50,
    "currency": "SGD"
  },
  "user_correction": "change price to 2.80"
}
```

**LLM Output (decimal — our code converts to integers before applying):**
```json
[
  {"op": "replace", "path": "/items/2/price", "value": 2.80}
]
```

**Validation rules (applied to patch before applying to display-form item):**
- Only `replace` operation allowed. Reject `add`, `remove`, `move`, `copy`.
- Path must match `/items/{n}/{field}` where `field` ∈ `{item_name, unit, unit_qty, price, currency, sku}`.
- `price` and `unit_qty` values must be positive numbers.
- Index `n` must be within bounds of `items` array.

**Apply order (canonical — patch always operates in display space):**
1. Apply validated patch to display-form item.
2. Validate resulting display-form item (all required fields present, types correct).
3. Convert entire display-form item to integer format via `money.py`.
4. Write integer item back to staging JSON.

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
│       ├── suppliers.py                ← NEW (global registry)
│       ├── restaurant_suppliers.py     ← NEW (link table)
│       ├── supplier_price_lists.py     ← NEW
│       ├── supplier_prices.py          ← NEW (BIGINT prices)
│       ├── file_processing_staging.py  ← NEW
│       └── handshake_requests.py       ← NEW
│
├── services/
│   ├── user_service.py
│   ├── restaurant_service.py
│   ├── supplier_service.py             ← NEW (create, list, fuzzy match, link)
│   ├── staging_service.py              ← NEW (state machine for uploads)
│   ├── price_service.py                ← NEW (confirm staging → write prices)
│   ├── context_service.py              ← NEW (read/write user.context JSONB)
│   ├── money.py                        ← NEW (decimal↔integer conversion helpers)
│   ├── entity_resolver.py
│   └── telemetry.py
│
├── llm/                                ← NEW
│   ├── intent.py                       ← Contract A
│   ├── parser.py                       ← Contract B
│   └── patcher.py                      ← Contract C
│
├── schemas/
│   ├── user.py
│   ├── restaurant.py
│   ├── supplier.py                     ← NEW
│   └── staging.py                      ← NEW
│
├── api/
│   └── v1/routes/
│       ├── auth.py
│       ├── restaurants.py
│       └── telegram.py
│
├── telegram/
│   ├── router.py                       ← NEW (6-priority router)
│   ├── handlers/                       ← NEW
│   │   ├── reset.py                    ← Priority 1
│   │   ├── buttons.py                  ← Priority 2
│   │   ├── commands.py                 ← Priority 3
│   │   ├── files.py                    ← Priority 4
│   │   ├── patterns.py                 ← Priority 5
│   │   └── llm_fallback.py             ← Priority 6
│   ├── keyboards.py                    ← NEW (inline keyboard builders)
│   ├── renderer.py                     ← NEW (message formatters; int→display)
│   ├── bot_api.py
│   ├── ack_handler.py
│   ├── ingest.py
│   └── commands.py
│
└── workers/
    ├── celery_app.py
    ├── telegram_tasks.py               ← calls router.py
    ├── ocr_tasks.py                    ← NEW (download + parse + convert + stage)
    ├── celery_types.py
    ├── db.py
    └── utils.py
```

---

## 7. Build Order

1. **DB models + migration** — `CREATE EXTENSION pg_trgm` first, then 6 new tables + restaurant location columns.
2. **`services/money.py`** — `to_minor(decimal, exp)`, `to_display(minor, exp)`, `infer_exp(currency)`. Everything touching prices depends on this.
3. **`services/context_service.py`** — `get_context()`, `set_context()`, `clear_context()`.
4. **`services/supplier_service.py`** — create (global), link to restaurant, list, fuzzy match.
5. **`telegram/router.py`** — skeleton dispatching to stubs.
6. **`telegram/handlers/reset.py`** — gets end-to-end routing working.
7. **`telegram/handlers/commands.py`** — `/list suppliers`, `/add supplier`, `/link supplier`, `/uploads`, `/switch`.
8. **`telegram/keyboards.py` + `telegram/renderer.py`** — shared formatting used by all handlers.
9. **`telegram/handlers/buttons.py`** — `rev_u`, `del_u`, `set_sup`, `new_sup`, `list_p`.
10. **`services/staging_service.py`** + **`telegram/handlers/files.py`** — upload flow with stubbed OCR.
11. **`llm/parser.py`** + **`workers/ocr_tasks.py`** — real parsing + decimal→integer conversion.
12. **`llm/patcher.py`** + `ed_row` in buttons — edit flow with conversion on apply.
13. **`services/price_service.py`** + `conf_u` — final write to `supplier_price_lists` + `supplier_prices`.
14. **`telegram/handlers/llm_fallback.py`** + **`llm/intent.py`** — LLM router last.

---

## 8. Error Handling & Safety

| Scenario | Handling |
|----------|----------|
| OCR/parse fails | `staging.status='error'`, `error_message` set, message: "Couldn't parse that file. Try a clearer photo or PDF." |
| LLM output fails schema validation | Log, set `staging.status='error'`, never write bad data |
| Decimal→integer conversion fails | Set `staging.status='error'`, do not write to staging |
| Button references deleted ID | Answer callback with alert: "This item no longer exists." |
| User attempts action on another user's staging | Reject: "You don't have permission to edit this upload." |
| Confirm without supplier set | Block: "Please identify the supplier first." with button list |
| Supplier not linked to restaurant | Create link automatically if user approved via handshake or `btn_set_supplier`. |
| Patch out of bounds / wrong type | Reject, reply: "I couldn't apply that edit. Please try again." |
| No active restaurant | Prompt `/switch` before any restaurant-scoped action |
| Near-duplicate supplier (similarity > 0.75) | Gate 1: "Did you mean {existing}? [Yes / No, create new]" — always user-confirmed |
| Supplier already linked | Gate 2: "You're already connected to {name}." — stop, no duplicate link |

---

## 9. Not in Phase 1

- Purchase orders
- Inventory tracking
- Multi-outlet brand grouping
- Analytics or reporting
- REST API routes for supplier/price data (Telegram only)
- Cross-supplier price comparison
