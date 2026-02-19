# Resto Pilot Roadmap

## Current snapshot (2026-02-19)
Phase 1 is in final hardening mode. All core features are shipped and test-covered (501 tests passing).

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

## Phase 2 - Purchase ordering
Goal: Create and track purchase orders to suppliers through chat.

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
