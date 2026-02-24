# Resto Pilot Roadmap

## Current snapshot (2026-02-24)
Phase 2 (Smart Procurement) is complete. 583 tests passing.
Phase 1 features remain fully intact.

### Done in this phase
- Telegram onboarding and routing pipeline.
- Supplier management and restaurant-scoped supplier linking.
- OCR pipeline for invoices and price lists (PDF + image paths).
- Review and confirm flows with inline keyboards.
- Invoice → inventory ledger + balance updates.
- Price list → supplier price upserts.
- Paginated command lists with button navigation.
- `/balance` item-level breakdown (negative and zero-stock sections).
- Session-aware telemetry wiring:
  - outgoing message logs
  - OCR/parser LLM call logs (usage metadata)
- Manual stock reconciliation via free-text shortcuts:
  - `"used 1kg onion"` → debit with confirm keyboard
  - `"2kg chicken left"` → set-balance with confirm keyboard
- Quick `[+]`/`[-]` ±1 adjust buttons on every `/inventory` row.
- Low-stock `⚠️` alerts shown after debit confirms.
- Natural language query routing (classify_and_answer + DB enrichment):
  - "how much onion do I have?" → answers from inventory context
  - "where can I buy chicken?" → routes to `/search chicken`
- `/search <query>` — unified cross-domain search (inventory + products + suppliers).
- `/history <item>` — last 10 transactions for an item with timestamps.
- `/export` — inventory CSV file download.
- `/chart` — visual per-unit-group stock bar chart (seaborn/PNG).
- `/help` refresh with categorised command sections.

### Still open before phase close
- Price-list meta display: wire `get_price_list_meta()` into `/prices` output (service ready, UI pending).
- Auth hardening: verify `_is_authorized_for_staging()` is called consistently on all staging-mutating callbacks.
- Keyboard-send error-path: graceful text fallback when `send_message_with_keyboard` returns non-2xx.

---

## Phase plan

## Phase 1 - Supplier + Pricing + Inventory Intake (current)
Goal: Restaurant staff can operate daily supplier and stock intake flows fully in Telegram.

Exit criteria:
- All `/help` command flows work end-to-end.
- Upload review/confirm loop is stable for invoices and price lists.
- Data written to final tables is consistent and tenant-safe.
- Operational telemetry is complete enough for debugging and cost attribution.

## Phase 2 - Smart Procurement ✅ COMPLETE (2026-02-24)
Goal: Close the procurement loop — par levels → reorder suggestions → purchase orders → receive.
Collapsed with Phase 4 (Intelligence layer) into one cohesive delivery.

### Shipped in Phase 2
- **Par levels** — `/par set <item> <qty> <unit>` (fuzzy item match, upsert idempotent)
- **Par view** — `/par` (paginated, ✅/⚠️/🚨 status icons per item)
- **Smart reorder** — `/reorder` (below-par items with best price + supplier buttons)
- **Purchase orders** — `/order <supplier>` (create draft), `/orders` (paginated list)
- **PO lifecycle buttons** — Submit (draft→sent), Mark received (sent→received), Cancel, Add item
- **Add-item text mode** — free-text "flour 10 kg" while `po_input_id` set in context
- **Auto-staging on receive** — PO receipt auto-creates `pending_review` staging record for the invoice review flow
- **Spend analytics** — `/spend` (current month by supplier, optional supplier filter for 3-month view)
- **Par-aware low-stock alerts** — after any debit (qadj or stock_conf), shows par gap + /reorder hint
- **NL routing** — /reorder, /orders, /par, /spend routed from natural language queries
- **Keyboard callbacks** — 6 new PO callbacks, all ≤ 64 bytes verified

### Test coverage
- `tests/test_par_service.py` — 14 tests
- `tests/test_purchase_order_service.py` — 18 tests
- `tests/test_keyboards.py` — 17 tests (callback sizes + keyboard shapes)
- `tests/test_commands_phase2.py` — 24 tests
- `tests/test_buttons_po.py` — 9 tests
- **Total: 583 passed, 1 skipped**

## Phase 3 - Expanded inventory operations
Goal: Stock movement workflows beyond invoice intake (consume, waste, adjustments, receiving states).

## Phase 4 - Intelligence layer
Goal: Better guided automation and proactive insights without sacrificing deterministic writes.

## Phase 5 - Multi-outlet and access controls
Goal: Scale workflows across multiple outlets and richer team roles.

## Phase 6 - Reporting and analytics
Goal: Supplier spend trends, price movement visibility, and actionable operational reporting.

---

## Product principles
1. Telegram-first UX.
2. Confirm-before-write for critical data changes.
3. Deterministic write paths with auditable telemetry.
4. Tenant isolation and membership checks on all mutating operations.
