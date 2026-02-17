# Resto Pilot — Product Roadmap

Resto Pilot is a Telegram-native operations assistant for restaurants.
Restaurant owners and staff manage suppliers, purchasing, and inventory
entirely through chat — no app install, no dashboard login required.

---

## Phase 0 — Clean Foundation (current)
**Goal:** Stable, minimal skeleton. Everything that isn't the core loop is gone.

- [x] Auth via Telegram WebApp token (JWT)
- [x] User + restaurant CRUD with membership model
- [x] Telegram webhook → instant ACK → Celery worker
- [x] Message and session telemetry (incoming + outgoing logged)
- [x] LLM call cost tracking infrastructure
- [ ] Alembic migration finalized and tested against clean schema
- [ ] All remaining tests green

**Outcome:** Deploy skeleton to staging. Webhook accepts messages, creates users, does nothing else yet.

---

## Phase 1 — Supplier & Catalog
**Goal:** A restaurant can manage its suppliers and the products they sell.

- Supplier onboarding via Telegram (`/add supplier`)
- Supplier product catalog (name, SKU, unit, price, currency)
- Price list upload (PDF/photo) → parsed and confirmed by user before saving
- Basic `/list suppliers`, `/list products` commands

**Data:**
- `suppliers` — name, contact, currency, notes
- `products` — name, unit, category
- `supplier_items` — links supplier ↔ product with price + effective date

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
