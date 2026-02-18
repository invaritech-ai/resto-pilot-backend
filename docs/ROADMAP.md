# Resto Pilot Roadmap

## Current snapshot (2026-02-18)
Phase 1 is in late hardening mode. Core supplier/price/invoice/inventory chat flows are implemented and test-covered.

### Done in this phase
- Telegram onboarding and routing pipeline.
- Supplier management and restaurant-scoped supplier linking.
- OCR pipeline for invoices and price lists (PDF + image paths).
- Review and confirm flows with inline keyboards.
- Invoice -> inventory ledger + balance updates.
- Price list -> supplier price upserts.
- Paginated command lists with button navigation.
- `/balance` item-level breakdown (negative and zero-stock sections).
- Session-aware telemetry wiring:
  - outgoing message logs
  - OCR/parser LLM call logs (usage metadata)

### Still open before phase close
- Final auth hardening on staging mutations (require active membership consistently).
- Production reliability hardening around Telegram keyboard-send failures.
- Remaining UX polish for review/confirm edge cases.
- Optional telemetry enhancements (cost backfill/reporting views).

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
