# Reads, Inventory Model & Upload Pipeline — Design Doc

**Date:** 2026-02-18
**Status:** Approved
**Author:** Architect session (Claude + Avi)

---

## Build Order

| Step | Scope | Branch |
|------|-------|--------|
| Current (step 6) | All read commands (8 slash commands, pagination) | `phase-1-step-6` |
| Step 7 | Inventory data model (new tables + Alembic migration) | `phase-1-step-7` |
| Step 8 | Upload pipeline (file → classify → OCR → staging → confirm) | `phase-1-step-8` |

Reads ship first. No new DB tables in the reads step — reads query existing tables only.

---

## Step 6: All Read Commands

### Command Set

| Command | Returns |
|---------|---------|
| `/profile` | Name, active restaurant, role, joined date |
| `/team` | Active members + pending invites for active restaurant |
| `/list suppliers` | Paginated supplier list *(existing, wired)* |
| `/outlets` | All restaurants user is a member of |
| `/products` | Confirmed price list items for active restaurant |
| `/prices <supplier>` | Current prices from that supplier |
| `/inventory` | All inventory items with current balance |
| `/balance` | Summary: total tracked items, zero/low stock alerts |

### Pagination Pattern (consistent across all list commands)

- Page size: 10 items
- Prev/Next via inline keyboard buttons (already stubbed in `buttons.py`)
- `set_numbered_items(user, [ids])` stores IDs in context for `#N` reply selection
- `set_list_state(user, list_type, offset)` tracks current page offset

```
/inventory
→ 1. Chicken Breast — 12.5 kg
  2. Olive Oil     — 4.0 L
  3. Tomatoes      — 8.2 kg
  ...
  [◀ Prev]  Page 1/3  [Next ▶]

Reply #N for item detail.
```

### Drill-down via `#N` reply (Priority 5 — pattern handler, already wired)

```
/inventory → reply "2"
→ Olive Oil
   Balance: 4.0 L
   Last credited: 2026-02-15 (Invoice INV-2023)
   Supplier: ABC Wholesalers
   Last price: $3.20/L
```

### Updated `/help` text

```
/profile           — Your profile
/team              — Staff & invites
/list suppliers    — Linked suppliers
/outlets           — Your restaurants
/products          — Supplier product catalog
/prices <name>     — Prices from a supplier
/inventory         — Stock levels
/balance           — Stock summary
/uploads           — Pending uploads
/switch            — Change restaurant
/help              — This message
```

### Implementation Notes

- All commands implemented in `app/telegram/handlers/commands.py`
- All require active restaurant (except `/profile` and `/outlets`)
- `/products` and `/prices` query `supplier_prices` (confirmed rows only)
- `/inventory` and `/balance` query `inventory_items` + `inventory_balances`
  — these tables do not exist yet; handlers return "No inventory data yet" stub until step 7
- One-user-one-restaurant assumption: `_require_active_restaurant()` auto-sets if user has exactly one restaurant

---

## Step 7: Inventory Data Model

### New Tables

#### `inventory_items`

```sql
CREATE TABLE inventory_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    name_lower      TEXT NOT NULL GENERATED ALWAYS AS (lower(name)) STORED,
    unit            TEXT,                        -- kg, litre, piece, dozen, etc.
    supplier_id     UUID REFERENCES suppliers(id),  -- nullable: item may predate any supplier link
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, name_lower)           -- no duplicate item names per restaurant
);

CREATE INDEX ix_inventory_items_restaurant ON inventory_items (restaurant_id);
CREATE INDEX ix_inventory_items_name_lower ON inventory_items USING GIN (name_lower gin_trgm_ops);
```

#### `inventory_transactions`

Immutable append-only ledger. Never updated after insert.

```sql
CREATE TABLE inventory_transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id),
    item_id         UUID NOT NULL REFERENCES inventory_items(id),
    txn_type        TEXT NOT NULL CHECK (txn_type IN ('credit', 'debit')),
    quantity        NUMERIC(12, 3) NOT NULL,      -- always positive; sign determined by txn_type
    unit_price      NUMERIC(12, 4),               -- nullable (manual entries may lack price)
    amount          NUMERIC(12, 2),               -- extracted total from invoice line item
    source          TEXT NOT NULL CHECK (source IN ('invoice', 'manual')),
    staging_id      UUID REFERENCES file_processing_staging(id),  -- nullable for manual
    notes           TEXT,
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_inv_txn_restaurant_item ON inventory_transactions (restaurant_id, item_id);
CREATE INDEX ix_inv_txn_staging ON inventory_transactions (staging_id);
```

#### `inventory_balances`

Running balance — updated atomically with every transaction. O(1) reads forever.

```sql
CREATE TABLE inventory_balances (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id),
    item_id         UUID NOT NULL REFERENCES inventory_items(id),
    balance         NUMERIC(12, 3) NOT NULL DEFAULT 0,
    last_txn_id     UUID REFERENCES inventory_transactions(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, item_id)
);
```

### Existing Table Changes

#### `suppliers` — add default currency

```sql
ALTER TABLE suppliers ADD COLUMN default_currency CHAR(3);  -- ISO 4217, e.g. USD
```

Set on first invoice confirmation if not already set. Future invoices from the same supplier resolve currency silently.

#### `file_processing_staging` — add document type

```sql
ALTER TABLE file_processing_staging
    ADD COLUMN document_type TEXT CHECK (document_type IN ('invoice', 'price_list'));
-- Nullable: set after user selects type post-upload
```

### Balance Write Pattern

Both the ledger entry and balance update commit in a single SQLAlchemy transaction. No drift possible.

```python
# InventoryService.record_transaction()
txn = InventoryTransaction(item_id=item_id, quantity=qty, txn_type='credit', ...)
db.add(txn)

delta = qty if txn_type == 'credit' else -qty
balance = db.scalar(
    select(InventoryBalance)
    .where(InventoryBalance.restaurant_id == restaurant_id,
           InventoryBalance.item_id == item_id)
    .with_for_update()
)
if balance:
    balance.balance += delta
    balance.last_txn_id = txn.id
    balance.updated_at = now()
else:
    db.add(InventoryBalance(restaurant_id=restaurant_id, item_id=item_id,
                            balance=delta, last_txn_id=txn.id))

db.commit()  # both committed atomically
```

Negative balances are permitted (edge case: returns, corrections). They are flagged in the `/balance` view with ⚠️.

### New Service

`app/services/inventory_service.py`

- `list_items(restaurant_id, offset, limit)` → paginated inventory items with balances
- `get_item(item_id)` → single item detail + last transaction
- `get_balance_summary(restaurant_id)` → total items, zero-stock count, negative-balance count
- `record_transaction(restaurant_id, item_id, txn_type, quantity, unit_price, ...)` → atomic ledger + balance write
- `fuzzy_match_item(restaurant_id, name, threshold)` → GIN trigram search on `inventory_items.name_lower`
- `confirm_invoice(staging_id, restaurant_id, resolutions)` → batch confirm with item resolution map

---

## Step 8: Upload Pipeline

### Flow

```
User uploads file (image or PDF)
    ↓
files.py: create staging record (document_type=NULL, status=processing)
    ↓
Bot: "What is this?  1️⃣ Invoice   2️⃣ Price list"
    ↓
User taps button → document_type set → Celery task dispatched
    ↓
Celery extraction (type-aware LLM prompt):
  image → Gemini Flash vision OCR → LLM structured parse
  PDF   → camelot + pdfplumber → LLM text parse
          → vision OCR fallback if extracted items < 5
    ↓
staging.extracted_data (JSONB) populated
staging.status → pending_review
    ↓
Bot: review message + inline keyboard (conf_u / ed_row / del_u)
    ↓
User taps conf_u
    ↓
System fuzzy-matches line items → inventory_items (threshold ≥ 0.8)
    ↓
All matched? → confirm directly
Unmatched?   → pause, show new-item review screen (see below)
    ↓
status → confirmed
```

### extracted_data JSONB Schema

```json
// Invoice
{
  "supplier": "ABC Wholesalers",
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
  "effective_date": "2026-02-01",
  "currency": "USD",
  "line_items": [
    {"name": "Chicken Breast", "unit": "kg", "unit_price": 8.50},
    {"name": "Olive Oil",      "unit": "L",  "unit_price": 3.20}
  ]
}
```

`amount` on invoice line items is extracted from the source document. On confirmation,
`abs(qty × unit_price - amount) > 0.01` flags the row with ⚠️ in the review screen.

### Currency Resolution

```
LLM extracts currency from document
  → found: use it
  → not found:
      supplier.default_currency set? → use it silently
      not set? → ask user once → save as supplier.default_currency
```

### New Item Review (anti-pollution gate)

When `conf_u` is tapped and unmatched items exist:

```
"Before confirming, 2 new items need review:

1. Canola Oil (2 L, $3.20)
   [➕ Add as new item]  [⬅️ Skip this row]

2. Chicken Brst — similar to Chicken Breast (85%)
   [✅ Use 'Chicken Breast']  [➕ Create 'Chicken Brst']"
```

Resolutions stored in `user.context["pending_item_resolutions"]` (transient, cleared on confirm or cancel).
Once all resolved, confirmation proceeds atomically.

Items below 0.5 similarity threshold → treated as new (no suggestion shown).
Items 0.5–0.8 → show closest match as suggestion.
Items ≥ 0.8 → auto-matched (no prompt).

### Error Handling

| Scenario | Handling |
|----------|----------|
| OCR returns < 3 items | Flag in review: "Only N items found — does this look right?" |
| `qty × unit_price ≠ amount` (> 1% delta) | ⚠️ flag that row; user can still confirm |
| Currency missing, no supplier default | Ask user once, save to `supplier.default_currency` |
| Celery extraction task fails | `staging.status → error`; bot: "Processing failed, try uploading again" |
| Balance goes negative | Allow; flag in `/balance` with ⚠️ |

---

## Testing Strategy

### Step 6 — Read Commands

**Unit tests:**
- Each read command handler (mocked DB)
- Pagination: correct offset, page size, numbered_items stored in context
- `/balance` summary: zero-stock and negative flags
- `#N` drill-down returns correct detail

**Acceptance criteria (QA):**
- `/profile` → correct name, restaurant, role
- `/team` → all active members shown
- `/outlets` → all restaurants user is member of
- `/inventory` → stub "No inventory yet" until step 7
- `/balance` → stub until step 7

### Step 7 — Inventory Data Model

**Unit tests:**
- `InventoryService.record_transaction()` — balance upsert, credit/debit sign
- Negative balance permitted, flagged in summary
- `fuzzy_match_item()` — threshold filtering
- `confirm_invoice()` — batch creates transactions + balances

**Integration tests (real DB / Neon):**
- INSERT transaction → assert balance updated atomically
- Two concurrent workers insert same item → no balance drift (FOR UPDATE)
- Full invoice confirm → verify row counts in inventory_transactions and inventory_balances

### Step 8 — Upload Pipeline

**Unit tests:**
- `extracted_data` schema validation (invoice vs price list)
- Amount cross-validation (qty × unit_price ≈ amount)
- Currency resolution logic (all three paths)
- New item review flow: resolution stored in context, resolved before confirm

**Integration tests (mocked OCR):**
- Full upload → classify → extract → review → confirm (invoice path)
- Full upload → classify → extract → review → confirm (price list path)
- Unmatched items → review screen shown → resolved → confirm proceeds
- Extraction failure → staging.status = error → user notified

---

## Key Assumptions

- One user = one active restaurant (for now). `_require_active_restaurant()` auto-sets if user has exactly one.
- Inventory quantities stored as NUMERIC(12,3) in display (decimal) space — not integer minor units. Inventory is not money.
- `inventory_transactions` is append-only (no UPDATE or DELETE). Corrections are new transactions.
- `inventory_balances` is the authoritative current stock. The ledger is the audit trail.
- Negative balances are valid (returns, adjustments) and are flagged, not blocked.
