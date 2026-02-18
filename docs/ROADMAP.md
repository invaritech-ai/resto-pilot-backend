# Resto Pilot — Product Roadmap

Resto Pilot is a Telegram-native operations assistant for restaurants.
Restaurant owners and staff manage suppliers, purchasing, and inventory
entirely through chat — no app install, no dashboard login required.

---

## Phase 0 — Clean Foundation ✅ Complete
**Goal:** Stable, minimal skeleton. Everything that isn't the core loop is gone.

- [x] Auth via Telegram WebApp token (JWT)
- [x] User + restaurant CRUD with membership model
- [x] Telegram webhook → instant ACK → Celery worker
- [x] Message and session telemetry (incoming + outgoing logged)
- [x] LLM call cost tracking infrastructure
- [x] Alembic migration finalized (single head, pg_trgm + pgvector extensions)
- [x] 229 tests green; deployed live on Neon (production branch only)

**Outcome:** ✅ Skeleton deployed. Bot accepts messages, creates users, sends ACK.

---

## Phase 1 — Supplier & Catalog (in progress)
**Goal:** A restaurant can upload supplier price lists, review parsed items, and confirm to save prices.

### ✅ Done
- User get-or-create on first message; `last_interaction_at` tracked
- 3-step onboarding flow (name → restaurant → welcome); owner membership always created
- Onboarding idempotent under Celery retry; commits before sending user messages
- `needs_onboarding()` catches webapp-auth bypass (full_name set but no restaurant)
- Reset/command words filtered from onboarding inputs; unknown step recovers gracefully
- 6-priority message router (`router.py`) wired into Celery worker
- `services/money.py` — `to_minor`, `to_display`, `infer_exp`, `QTY_EXP=3`
- `services/context_service.py` — JSONB read/write/clear with navigation state
- `services/supplier_service.py` — global registry, link to restaurant, fuzzy match
- `services/restaurant_service.py` — create (with owner membership), members list
- All DB models + migration (suppliers, restaurant_suppliers, supplier_prices, supplier_price_lists, file_processing_staging, handshake_requests)

### 🔧 In Progress (current branch: `phase-1-step-6`)
- Steps 6–9: reset handler, commands handler, keyboards, renderer, button handler (reads only)

### ⏳ Remaining
- Steps 10–11: file upload handler + OCR Celery task (pdfplumber → LLM parser)
- Steps 12–13: correction patch flow + `conf_u` confirm → write to `supplier_prices`
- Step 14: LLM fallback intent classifier

**Data model (all migrated):**
- `suppliers` — global registry with trigram search
- `restaurant_suppliers` — link table (per-restaurant)
- `supplier_price_lists` — upload header records
- `supplier_prices` — BIGINT minor-unit prices, append-only
- `file_processing_staging` — working state during upload review

---

## Phase 1.5 — Product Catalog
**Goal:** Normalize raw item names from price lists into a canonical product catalog. Enables cross-supplier price comparison and clean order line items.

- `products` table: canonical name, unit, category, per-restaurant
- Link `supplier_prices` rows to `product_id` (optional at ingest, resolved later)
- `/list products` — browse catalog
- Fuzzy match at ingest: suggest canonical product for each new item name
- Cross-supplier query: "cheapest tomato across my suppliers"

**Data:**
- `products` — id, restaurant_id, name, name_lower, unit, category, is_active
- Amend `supplier_prices` — add nullable `product_id FK → products`

---

## Phase 2 — Purchase Orders
**Goal:** A restaurant can place and track orders to suppliers.

- Create a purchase order by chatting: "order 5 cases of tomatoes from X"
- Supplier confirmation workflow (forward order summary)
- Order status tracking (pending → confirmed → delivered)
- Simple delivery log

**Data:**
- `purchase_orders` — restaurant, supplier, status, ordered_at
- `purchase_order_lines` — product, quantity, unit_price

---

## Phase 3 — Inventory
**Goal:** Know what's in stock at all times.

- Receive stock against a purchase order (marks items delivered)
- Ad-hoc stock adjustments (waste, transfer, stocktake)
- Low-stock alerts via Telegram
- Per-location tracking (optional, single-location first)

**Data:**
- `inventory_batches` — product, quantity, location, received_at
- `inventory_movements` — batch, delta, reason, recorded_at

---

## Phase 4 — Intelligence Layer
**Goal:** Natural language first. Users don't need to know commands.

- Intent planner: classify user messages → deterministic action
- Fuzzy entity resolution (supplier names, product names)
- Document ingestion: invoices parsed and reconciled against POs
- Proactive alerts: price changes, overdue deliveries

**Architecture:**
- Cheap LLM for ACK + intent (fast, low cost)
- Structured tool execution (no free-form LLM output in DB writes)
- Human confirmation before any write action

---

## Phase 5 — Multi-Outlet & Teams
**Goal:** One account, many restaurants, role-based access.

- Staff invite flow
- Per-restaurant access control (owner vs staff)
- Outlet-level vs group-level reporting

---

## Phase 6 — Analytics & Reporting
**Goal:** Actionable numbers without leaving Telegram.

- Weekly cost summary per supplier
- Price trend alerts (supplier raised prices)
- Wastage reports
- Monthly COGS estimate

---

## Principles

1. **Telegram-native** — every feature must work in chat. No fallback to a web UI.
2. **Confirm before write** — AI can read freely, never writes without user confirmation.
3. **Cheap to run** — minimize LLM calls; prefer deterministic code over inference.
4. **One restaurant at a time** — features ship per single-outlet first, then multi-outlet.
