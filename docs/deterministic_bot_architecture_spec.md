# Deterministic Bot Architecture Spec (No Tool Loops)

Goal: **LLM only decides (a) whether context is sufficient and (b) which single tool to call with what args.** All operations are deterministic, validated, permission-checked, and telemetry-recorded.

This spec is written against the current codebase (not yet implemented changes).

---

## Invariants

1. **At most 1 tool call per user message.** No function-calling loops.
2. **No user-visible IDs.** Users reference entities by natural language; IDs stay internal.
3. **Deterministic resolution rules.** If ambiguous, ask a numbered question; never guess.
4. **All LLM calls recorded in telemetry** (`LLMCalls`), tagged by purpose: `ack`, `planner`, `clarifier`, `presenter`.
5. **Tool outputs are structured JSON**, not prose; the Presenter is the only component that generates user-facing text.
6. **No session JSON bloat.** Cache stores query + cursors + ID lists, and uses follow-up DB calls for paging/details.
7. **Writes execute immediately** (no confirm/commit) **except file uploads**, which remain review/confirm flows.
8. **Planner reasons over tool catalog, not DB schema.** Database details are encapsulated inside tools and deterministic resolver utilities.

---

## End-to-end Flow (Single Path)

### Normal message
1. Receive message.
2. `AckLLM` (optional) → send ack → record telemetry.
3. `PlannerLLM` (required) → returns either `clarify` or `call_tool` → record telemetry.
4. Deterministic validation gate:
   - Validate planner output against tool catalog (tool exists, args schema).
   - Enforce “one tool call” and “no IDs”.
   - For **writes/link/unlink/rename**, require strict entity resolution; otherwise force `clarify` with numbered choices.
4. If `clarify`:
   - Store `pending_action` / `pending_selection` in context.
   - Send question (usually numbered) (Clarifier may be used for phrasing).
5. If `call_tool`:
   - Execute exactly **one** tool.
   - Store result metadata into context cache (IDs/cursor only).
   - `PresenterLLM` formats response text → send → record telemetry.

### File upload
1. Receive message with file.
2. If file kind unknown: ask “What kind of file is this?” (deterministic question, numbered).
3. Tool: `files_process(kind, restaurant_query, file_id, supplier_query?)` → creates staging.
4. Tool: `files_review(staging_id)` → show sample (deterministic truncation rules).
5. User confirms/edits:
   - Tool: `files_update_field(...)` as needed
   - Tool: `files_confirm(staging_id)` writes to DB
6. `PresenterLLM` formats confirmation summary.
7. **No manual supplier creation:** if a supplier is missing during a link/update request, the clarifier directs the user to upload a supplier price list to add it.

---

## Context Storage

### Current
- `users.state_data` is the active context store (`active_restaurant_id`, `active_supplier_id`, `pending_action`).
- `telegram_sessions` currently has no JSON context column.

### Proposed (minimal bloat)
Use both:
- `users.state_data`: durable “who/where am I” + pending flows.
- `telegram_sessions.context_json`: session-scoped cache + last 10–20 message rollup + disambiguation state.

**Reason:** user profile context should not grow with paging caches; session cache should be easy to flush.

---

## Proposed Table Changes (DB schema)

### `telegram_sessions`
Add:
- `context_json` (JSON) — session-scoped cache/context (paging, pending selection, recent turns summary).
- `context_updated_at` (datetime) — simple pruning/cleanup logic.
- (Optional) `context_version` (int) — safe migrations of context structure.

Why:
- Avoid bloating `users.state_data`.
- Enables deterministic “more/open 2/last” without LLM.

### `invite_codes` / `restaurants` (optional, depends on desired “soft delete”)
If you truly want “soft delete”, the current schema doesn’t support it.
- `invite_codes`: add `revoked_at` (datetime) OR `is_active` (bool).
- `restaurants`: add `archived_at` (datetime) OR `status` enum.

Why:
- Current tools can only hard-delete invite rows, and restaurants have no soft-delete fields.

---

## Deterministic Entity Resolution (No IDs)

Create a shared resolver used by tools (not LLM):

### Resolver API (internal)
`resolve_entity(kind, query, scope) -> ResolutionResult`

`ResolutionResult`:
- `status`: `resolved | ambiguous | not_found`
- `id` (when resolved)
- `candidates`: list of `{id, display}` (when ambiguous; capped)
- `question`: deterministic question text (optional)
- `list_token`: opaque token stored in session context for numeric selection

### Matching rules (deterministic)
1. Normalize: lowercase, trim, collapse whitespace, strip punctuation; domain-specific aliasing (e.g. “co.” → “company”).
2. Exact normalized match → resolved.
3. Substring/prefix match with fixed thresholds.
4. Fuzzy match with fixed cutoffs (accept only if best score ≥ X and best-second_best ≥ Y).
5. Else: ambiguous or not_found → ask numbered question.

### Stricter rules for writes
For **writes/link/unlink/rename** operations, accept a match only if it is unambiguous and above a higher confidence threshold; otherwise return `ambiguous`/`not_found` and force a numbered clarification question. Never “best guess” on mutations.

### Numeric selection parser (deterministic, no LLM)
Handle:
- `"1"`, `"2"`, `"10"`
- `"first"`, `"second"`, `"last"`, `"next"`
- `"open 2"`, `"supplier 3"` style follow-ups

Selection is only valid if there is an active `pending_selection` in context.

---

## Cache / Paging (No JSON bloat)

Store minimal cache entries in `telegram_sessions.context_json`:

### `cached_lists`
Each entry:
- `cache_key`: uuid
- `kind`: e.g. `supplier_items_search`, `invoices_list`, `suppliers_list`
- `query`: the original args (without secrets)
- `ordered_ids`: list of UUID strings (small page only) OR `cursor` for next fetch
- `page_size`: int
- `created_at`: iso datetime

### Deterministic follow-ups (no LLM)
- `more`: fetch next page using stored cursor and same query.
- `open N`: fetch details for the Nth entry on the current page via DB.
- `first/last`: map to index deterministically.

---

## LLM Modules (4 required)

All modules must record telemetry via `record_llm_call(purpose=...)`.

### 1) AckLLM (optional)
Purpose: quick acknowledgment while planning (or for perceived responsiveness).
Input:
- `message_text`, `has_file`, `file_kind?`
Output (strict JSON):
```json
{"send_ack": true, "text": "Got it—one moment."}
```
Notes:
- Can be replaced by deterministic templates to reduce LLM cost.

### 2) PlannerLLM (required; single decision point)
Purpose: decide whether to clarify or call exactly one tool with args.
Input (structured JSON):
- `recent_turns` (last ~10–20: user+bot)
- `context` (active restaurant/supplier, pending_action, pending_selection)
- `available_tools` (names + arg schema + short descriptions)
- `hard_rules` (one tool max; no IDs; supplier creation via upload only; strict resolution for writes)
Output (strict JSON, one of):
```json
{"action":"clarify","clarify_kind":"restaurant|supplier|item|date|...", "question":"...", "choices":[{"label":"1","text":"..."}, {"label":"2","text":"..."}]}
```
or
```json
{"action":"call_tool","tool":"supplier_items_search","args":{"item_query":"pork","restaurant_query":null,"supplier_query":null,"limit":10}}
```
Hard constraint:
- Planner must never return multiple tool calls.

### 3) ClarifierLLM (optional but requested)
Purpose: phrasing only. Convert deterministic candidates into a clean numbered question.
Input:
- `clarify_kind`, `instruction`, `candidates[{display,...}]`
Output:
```json
{"question":"Which supplier do you mean? Reply with a number.","choices":[{"label":"1","text":"Cheong Hing Company"},{"label":"2","text":"Cheong Hing Foods"}]}
```
Notes:
- This could be deterministic instead; keep only if you want tone/clarity improvements.

### 4) PresenterLLM (required)
Purpose: turn tool JSON into a user response (short, consistent, Telegram-friendly).
Input:
- `user_message`
- `tool_result` (JSON string)
- `presentation_rules` (e.g., max lines, include outlet name, show currency)
Output:
```json
{"text":"Here are 10 pork items…\\n1) …\\n2) …","followups":["Reply `more` for next page","Reply `open 2` for details"]}
```

### Optional additional LLM modules (only if needed)
- **Onboarding/Help Composer**: generate help text based on enabled tools (could replace static help).
- **Tone/Localization**: if you need multi-lingual output or consistent brand voice.
Recommendation: keep these optional; they do not affect tool determinism.

---

## Tools: What must be Added / Rewritten / Removed

### A) New tools (needed for CRUD completeness / deterministic UX)
Based on current business tables and your permissions table:

**Restaurant membership (`restaurant_users`)**
- `invite_codes_redeem(code)` (join via invite without requiring `/start CODE`).
- `restaurant_members_update(restaurant_query, member_query, role?, status?)` (owner-only for role/status changes).

**Invite codes (`invite_codes`)**
- `invite_codes_move(code, restaurant_query)` (owner-only).
- (If “soft delete” is required) `invite_codes_revoke(code)` should set `revoked_at` instead of deleting (requires schema).

**Invoices (`invoices`, `invoice_line_items`)**
- `invoices_list(restaurant_query, supplier_query?, date_from?, date_to?, limit?, cursor?)`
- `invoices_get(invoice_ref)` (by number or resolved ID; includes line items)

**Supplier catalog / pricing**
- `supplier_items_search(item_query, restaurant_query?=null, supplier_query?=null, limit?, cursor?)`
- `supplier_prices_list_current(supplier_query? or item_query?, restaurant_query?=null, limit?, cursor?)`

**Restaurant–supplier link management (`restaurant_suppliers`)**
- `restaurant_suppliers_unlink(restaurant_query, supplier_query)` (owner-only; sets link status inactive)
- `restaurant_suppliers_update_link(restaurant_query, supplier_query, patch...)` (owner-only for status/metadata)
- If supplier is not found, the system must **not** offer manual creation; it must prompt for a supplier price list upload.

### B) Existing tools to rewrite (edited)
**Current tool resolver loop**
- Replace `app/ai/tool_resolver.py` loop with a deterministic orchestrator:
  - `planner -> (clarify | single tool call) -> presenter`
  - enforce “one tool per message”.

**Supplier link tool**
- Split `link_suppliers` (currently does create+update+unlink) into:
  - `restaurant_suppliers_link` (staff+owner)
  - `restaurant_suppliers_update_link` / `restaurant_suppliers_unlink` (owner-only)

**File processing tools**
- Optionally unify `process_invoice_file`, `process_price_list_file`, `process_inventory_photo` into `files_process(kind, ...)`.

### C) Code paths to remove
- Item-search fast-path:
  - `app/conversation/item_search_router.py` and its invocation in `app/conversation/processor.py`
  - any “search session” logic that bypasses the single deterministic orchestrator

### D) Support functions (internal, reusable; new)
- `app/domain/services/entity_resolver.py` (or similar):
  - normalize + deterministic fuzzy matching
  - returns resolved/ambiguous/not_found structures
- `app/conversation/selection_parser.py`:
  - parse `"2"`, `"second"`, `"last"`, `"open 3"`, `"more"`
- `app/conversation/session_cache.py`:
  - read/write minimal cache entries in `telegram_sessions.context_json`

---

## Answers to Open Questions

### Is TTL necessary?
Not strictly. A safe deterministic approach is **conversation-state gating**:
- Numeric/ordinal replies are only accepted if there is an active `pending_selection` and the previous bot message requested a numbered reply.

TTL is optional and mainly useful for housekeeping (cleanup of stale cached lists/selections), not for correctness.

### Is numeric resolution (“2”, “last one”, etc.) supported by this design?
Yes. The key is:
- Always ask questions with numbered choices.
- Store `{selection_token -> candidates[]}` in session context.
- Parse numeric/ordinal replies deterministically, without LLM.

### Will rolling context + cache metadata help Planner decide if context is enough?
Yes, as long as you provide Planner structured context:
- active restaurant/supplier (if any)
- pending clarification type (if any)
- last cached list kind + what it represents (not the full rows)
- last ~20 turns (user+bot)

Planner should not need the full dataset; it only needs to decide whether to call a tool and with which args.
