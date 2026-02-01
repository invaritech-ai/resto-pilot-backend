# Telegram item search MVP plan

## Summary

Add a Telegram bot feature that supports:

- Query: `search item <query>` (and natural-language equivalents like “find item …”).
- Quick filters (simple, typed tokens like `supplier:`, `outlet:`, `status:`).
- A short, paginated list of results.
- Follow-up prompts:
    - “show more” / “more”
    - “open item <n>” (open details for result #n from the last search)
- Two entry modes:
    - **Standalone search** (default): find an item to view details.
    - **Order flow**: user says “order food” → bot asks “what would you like to order?” → runs the same search engine but lists results with **price + supplier** first.
- A single cheap parse LLM call (new searches only) that turns natural language like “meat products with smoked” into a small set of concrete query variants + filters, followed by pure DB lookups.

This repo is an “intent-driven static bot” whose default message path is `app/conversation/processor.py` → `app/ai/tool_resolver.py` → DB tools in `app/ai/db_tools/*`, with lightweight per-user context in `user.state_data` (`app/conversation/context.py`). This feature adds a small pre-tool-resolver fast path for item search to minimize LLM calls and keep formatting deterministic.

---

## Goals (MVP)

1. **Fast item lookup** inside a restaurant account from Telegram.
2. **Minimal, predictable UX**: numbered results, short instructions, deterministic pagination.
3. **Safe & permissioned**: never leak IDs; only show items in outlets the user can access.
4. **Low added cost**: one cheap parse LLM call per new search; follow-ups are 0 LLM calls; formatting is deterministic text.

---

## Non-goals (explicitly out of scope for MVP)

- True semantic search / embeddings.
- Rich Telegram UI (inline keyboards, deep links, web app).
- Bulk edits from search results.
- Full-text ranking and typo tolerance beyond basic query variants (can be added later via Postgres trigram/FTS).
- Multi-step guided filtering UI (MVP uses typed filters only).

---

## What is an “item” in this MVP?

The codebase has multiple “item-like” entities:

- `products` (canonical product master, restaurant-scoped)
- `supplier_items` (supplier-specific catalog rows, supplier-scoped)
- `inventory_batches` (stock entries, product+supplier scoped)

**MVP definition**: treat **Products** as the primary “item”, but surface the most relevant supplier context when available.

Search should match:

- `Products.name_en` (primary)
- (optional) `Products.name_local`
- (optional, secondary) `SupplierItems.supplier_name_raw` when a supplier is selected, to help users find products via supplier naming

Details (“open item”) should show:

- Product name
- Outlet (active outlet context, or resolved outlet filter)
- A short inventory snapshot (optional: on-hand batch count, or most recent batch received)
- Supplier coverage (optional: list of linked suppliers that have supplier_items mapped to this product, if any exists)

If this “product-first” scope is too narrow, we can extend later to a dual-mode search:

- `search supplier item …`
- `search product …`

---

## UX specification

### Modes

- **Standalone search**: user is browsing/looking up items.
- **Order flow**: user is trying to pick something to order; results are optimized for purchase decisions (price + supplier + contact details).

### Entry points

1. Explicit command:

- `search item tomatoes`

2. Natural language:

- “search tomatoes”
- “find item tomatoes”
- “look up tomatoes”
- “show me meat products with smoked”

3. Order flow:

- “order food”
- “place an order”
- “I want to order”

### Filters (MVP tokens)

All filters are optional and can appear anywhere:

- `outlet:<name-or-number>`
    - Uses the same outlet selection semantics as other features: match active outlet context if omitted; if ambiguous, ask to pick an outlet.
- `supplier:<name>` (optional)
    - Narrows results to products that appear in that supplier’s catalog (via `supplier_items.product_id`).
- `status:active|inactive`
    - Defaults to `active`.
    - Applies to `Products.is_active` (and optionally `Suppliers.is_active` when supplier filter is present).
- `limit:<n>`
    - Per-page size. Default 5, max 10.

Examples:

- `search item tomato outlet:Mercato`
- `search item chicken supplier:Fresh Farms`
- `search item basil status:active limit:10`
- `show me meat products with smoked outlet:Mercato`

### Response format (list)

Return a short numbered list (default 5):

- Header: `🔎 Items (5 of 23)` (counts optional if expensive; can show `5 shown` in MVP)
- Body: `1. Roma Tomato (Dry)` (storage type optional; keep short)
- Footer instructions:
    - `Reply “more” to see the next results.`
    - `Reply “open 2” to see item #2.`
    - `Reply “search item <new query>” to start over.`

#### Order flow list variant

When in **order flow**, list results with price + supplier, sorted by price when available:

- Header: `🛒 Order options (5 shown)`
- Body example:
    - `1. Pork Belly — $12.50/kg — Fresh Farms`
    - `2. Pork Shoulder — $13.00/kg — Cheong Hing`
- Footer instructions:
    - `Reply “open 2” to see item details.`
    - `Reply “supplier 1” to see supplier details.`
    - `Reply “more” to see more options.`

### Pagination behavior

Stateful pagination uses the user context (`user.state_data`):

- After any successful search, store a `pending_action` representing the “active search session”.
- `more` / `show more` uses the stored query+filters and advances offset.
- If there are no more results, respond: “No more results. Try a new search.”

### “Open item” behavior

`open 2` or `open item 2`:

- Valid only if a search session exists in context.
- Uses the Nth result from the last page (or last full result list, see state model below).
- Respond with a short details view and suggested next prompts:
    - “search item …”
    - “more”
    - (future) “show suppliers”, “show inventory”, “add to inventory”

### “Supplier details” behavior (order flow)

`supplier 2` or `supplier details 2`:

- Valid only if an order-flow search session exists and the selected row includes a supplier.
- Respond with supplier contact info (from `suppliers` and, when relevant, `restaurant_suppliers`):
    - supplier name
    - contact name, phone, email
    - (optional) account number / notes for this outlet
- No UUIDs in user-facing responses.

---

## State model (context)

Use `UserContext.pending_action` (see `app/conversation/context.py`) with a dedicated payload:

```json
{
    "type": "item_search",
    "mode": "search",
    "query_plan": {
        "raw": "show me meat products with smoked",
        "queries": ["smoked pork", "smoked beef", "smoked chicken"],
        "include_terms": ["smoked"],
        "exclude_terms": [],
        "category_hints": ["meat"]
    },
    "restaurant_id": "…",
    "supplier_id": null,
    "status": "active",
    "limit": 5,
    "offset": 0,
    "last_page": [
        { "product_id": "…", "label": "Roma Tomato" },
        { "product_id": "…", "label": "Cherry Tomato" }
    ],
    "has_more": true,
    "created_at": "2026-01-31T00:00:00Z"
}
```

Notes:

- Do not store raw UUIDs in user-facing messages, but storing `product_id` in context is fine.
- Keep `last_page` small (max 10).
- `created_at` enables a simple TTL: if older than (say) 30 minutes, ignore and ask user to re-search.
- The stored `query_plan` is used for pagination so “more” does not call the LLM.

Order-flow sessions reuse the same structure with `mode: "order"`, and `last_page` includes supplier/price fields:

```json
{
    "type": "item_search",
    "mode": "order",
    "restaurant_id": "…",
    "limit": 5,
    "offset": 0,
    "last_page": [
        {
            "product_id": "…",
            "label": "Pork Belly",
            "supplier_id": "…",
            "supplier_name": "Fresh Farms",
            "price": 12.5,
            "currency": "USD",
            "unit": "kg"
        },
        {
            "product_id": "…",
            "label": "Pork Shoulder",
            "supplier_id": "…",
            "supplier_name": "Cheong Hing",
            "price": 13.0,
            "currency": "USD",
            "unit": "kg"
        }
    ]
}
```

---

## Technical design

### Preferred flow: conversation-level fast path (lowest cost)

The default bot path (`resolve_with_tools(...)` + final response composer) may involve multiple LLM calls. For item search, we want:

- **New searches**: exactly **one** cheap LLM call (parse only).
- **Follow-ups** (“more”, “open 2”): **zero** LLM calls.
- **Formatting**: deterministic output (no response-composer LLM).

Implement an “item search router” that runs in `app/conversation/processor.py` _before_ calling `resolve_with_tools(...)`.

High-level behavior:

1. If message is a follow-up and `context.pending_action.type == "item_search"`:

- `more` / `show more` → DB search for next page (no LLM)
- `open <n>` / `open item <n>` → load details for `last_page[n-1]` (no LLM)

2. Else if message looks like a new item search request:

- Parse typed `key:value` tokens deterministically.
- Run one cheap parse LLM call to generate a small `query_plan` (query variants + include/exclude + category hints).
- Execute DB lookups using the query plan and return a formatted list.
- Store `pending_action` for pagination.

3. Else if message is “order food” (or similar) and there is no active order session:

- Set `pending_action.type = "order_food_prompt"` and respond: “What would you like to order?”
- Next user message is treated as a new search request but with `mode="order"`.

### Where the code lives (minimal-new-file layout)

- `app/ai/item_search_query_planner.py` (new): single cheap parse step (strict JSON output)
- `app/domain/services/item_search_service.py` (new): DB queries + merge/dedupe + formatting helpers
- `app/conversation/processor.py`: early routing hook + context update

### Deterministic token parsing (ground truth)

Parse and apply these tokens before calling the parse LLM (and remove them from the semantic text):

- `outlet:<name-or-number>`
- `supplier:<name>`
- `status:active|inactive`
- `limit:<n>` (default 5, max 10)

Rationale: this keeps power-users happy and prevents the LLM from “mis-parsing” explicit filters.

### Cheap parse LLM: query planner contract

Use a small model (recommended: `get_intent_model(settings)`) to output strict JSON only.

Output shape:

```json
{
    "queries": ["smoked pork", "smoked beef", "smoked chicken"],
    "include_terms": ["smoked"],
    "exclude_terms": [],
    "category_hints": ["meat"]
}
```

Constraints:

- Hard cap: `queries` length 1–3.
- Hard cap: include/exclude length 0–5.
- If uncertain, return `queries: [<cleaned user query>]` only.
- Never output IDs.

Cost controls:

- `temperature=0`
- `max_tokens` small (e.g. 120–200)
- Short timeout (e.g. ≤ 6s)
- Only call for _new searches_ (never for “more/open”).

### DB execution: loop through queries, merge, and paginate

Data access patterns:

1. Determine effective outlet scope:

- If `outlet:` is provided, resolve restaurant by name/number and validate access.
- Else if user has `active_restaurant_id`, use it.
- Else if user has exactly 1 outlet, auto-select it.
- Else ask “Which outlet?” and do not run the parse LLM yet.

2. Resolve supplier (optional):

- If `supplier:` token exists, resolve supplier within the selected outlet (via `restaurant_suppliers`).

3. Execute a “multi-query” loop (at most 3 DB queries):

- For each `query_plan.queries[i]`, run a query against `Products` in the outlet:
    - `Products.name_en ILIKE %query%` (and optionally `name_local`)
    - apply `status` (`Products.is_active`)
    - if `supplier_id` is present, restrict to products present in `supplier_items` for that supplier
- Fetch up to a small bounded number per variant (enough to cover `offset + limit + 1`, capped e.g. 60).
- Merge and dedupe by `product_id`.
- Rank deterministically by:
    1. variant priority (i=0 highest)
    2. match quality (exact/prefix/contains)
    3. alphabetical by name
- Compute:
    - `page = merged[offset : offset+limit]`
    - `has_more = len(merged) > offset + limit` (best-effort; good enough for MVP)

#### Order-flow pricing enrichment (same engine, extra joins)

When `mode == "order"`:

- For each candidate product, compute a “best offer” row:
    - Join `supplier_items` where `supplier_items.product_id == products.id`
    - Join `suppliers` (for supplier name + contact info)
    - Join `restaurant_suppliers` to ensure supplier is linked to the selected outlet and active
    - Join `supplier_prices` to get a current price (MVP: latest `valid_from` with `valid_to` null or in the future)
- Choose the **lowest** current price per product as the display row (ties: alphabetical supplier name).
- Sort results by:
    1. products with a price first (ascending price)
    2. then products without a price (alphabetical)

If no prices exist for a product, show “Price unavailable” but still list it (unless stakeholders prefer to hide them in order mode).

### Response formatting (deterministic)

Build the Telegram message text directly (no LLM):

- List response: header + numbered items + footer prompts (“more”, “open <n>”).
- Detail response: short product header + optional small inventory/supplier summary.
- Order-mode list response: include `price` + `supplier_name` per row; footer includes `supplier <n>`.
- Supplier-details response: formatted from `suppliers` and `restaurant_suppliers` (contact fields, account number, notes).

### Fallback: tool-resolver implementation (optional/later)

If you prefer item search to remain “just another tool” later:

- Add `app/ai/db_tools/items.py` and wire it into `app/ai/db_tools/base.py`.
- Add fast-path routing in `app/ai/tool_resolver.py`.

This is higher runtime cost (tool-resolver LLM + final response LLM), so it is not the MVP recommendation for “minimal added cost”.

---

## Acceptance criteria

### Search

- Given a user with access to an outlet that has products, `search item tomato` returns:
    - A numbered list of up to `limit` results.
    - A prompt explaining `more` and `open <n>`.
    - Stores `pending_action.type == "item_search"` with enough info to paginate.
    - Runtime cost: **1** cheap parse LLM call; **0** response-formatting LLM calls.

### Order flow

- Given a user says “order food”, bot asks: “What would you like to order?”
- Given the user answers with a query, bot performs the same search but:
    - Lists results with **price + supplier name** when available
    - Sorts by lowest price first (priced items before unpriced)
    - Supports `supplier <n>` to show supplier contact info
- “more” / “open <n>” / “supplier <n>” are all **0** LLM calls.

### More

- After a search, user sends `more` and gets the next page.
- If there are no more, user gets a clear “no more results” message and the search session is cleared (or marked `has_more=false`).
- Runtime cost: **0** LLM calls.

### Open

- After a search, user sends `open 2` and gets details for result #2.
- If user sends `open 99` with fewer results, they get a helpful error and the list of valid indices.
- Runtime cost: **0** LLM calls.

### Safety

- No UUIDs shown in user-facing responses.
- Searching an outlet the user cannot access returns a permission error.

---

## Implementation steps (granular)

### Phase 0 — Spec & wiring (no behavior change)

1. Create `docs/telegram-item-search-mvp-plan.md` (this doc).
2. Decide the canonical “item” entity (MVP: `Products`), and confirm with stakeholders.

### Phase 1 — Deterministic token parser

1. Implement a small token parser for `outlet:`, `supplier:`, `status:`, `limit:`:

- Extract tokens and also produce a “semantic text” with tokens removed.
- Keep behavior deterministic and unit tested.

### Phase 2 — Query planner (single cheap LLM call)

1. Add `app/ai/item_search_query_planner.py`:
    - `plan_item_search(semantic_text, *, settings) -> QueryPlan`
    - Strict JSON schema + hard caps + validation.
2. Add fallback behavior:
    - If planner fails/invalid JSON → `queries=[semantic_text]`.

### Phase 3 — DB search + formatting service

1. Add `app/domain/services/item_search_service.py`:

- `search_products(...)` implements the multi-query loop, merge/dedupe, ranking, and pagination.
- `search_products_with_best_offer(...)` (order mode) enriches with best (lowest) current price + supplier.
- `format_search_results(...)` and `format_item_details(...)` produce Telegram-ready text (no LLM).
- `format_order_results(...)` and `format_supplier_details(...)` (order mode).

2. Add a product-details loader for “open item” (permission-checked).
3. Add a supplier-details loader (permission-checked; supplier must be linked to the active outlet).

### Phase 4 — Conversation routing (fast path)

1. Update `app/conversation/processor.py`:

- Intercept new item searches and run: token parse → query planner (1 LLM) → DB search → deterministic formatting.
- Intercept follow-ups:
    - `more` / `show more` uses stored `pending_action` (0 LLM)
    - `open <n>` uses stored `last_page` (0 LLM)
    - `supplier <n>` uses stored `last_page[n-1].supplier_id` (0 LLM, order mode only)
- Add “order food” entry:
    - If message indicates order intent, ask “What would you like to order?” and set a pending prompt state.
    - The next message runs search with `mode="order"`.

2. Add TTL behavior for search sessions (recommended: 30 minutes).

### Phase 5 — Tests

Add unit tests using the existing in-memory SQLite patterns in `tests/*`:

1. `tests/test_item_search_basic.py`
    - Creates user + restaurant + products.
    - Asserts list formatting and `pending_action` shape.
2. `tests/test_item_search_filters.py`
    - Adds supplier + restaurant_suppliers + supplier_items mapping.
    - Asserts `supplier:` filter narrows results.
3. `tests/test_item_search_followups.py`
    - Simulates pending_action state and calls the fast paths (“more”, “open 1”).
4. `tests/test_item_search_query_planner_contract.py`
    - Mocks the OpenAI client response and validates caps/schema/fallback behavior.

### Phase 6 — Docs / intents

1. Add the new capability to `docs/intents-and-paths.md` (even if it’s tool-routed, it’s still a supported journey):
    - “Search items” path
2. (Optional) Update `docs/telegram-bot-commands.md` if we introduce a `/search` command later.

### Optional later — Tool-based search

If you decide not to add a processor-level fast path, or you want search to be callable as a tool:

1. Add `app/ai/db_tools/items.py` and wire into `app/ai/db_tools/base.py`.
2. Add “more/open” routing in `app/ai/tool_resolver.py`.

---

## Open questions (decisions needed)

1. **Definition of “item”**: product-only vs supplier_item-first vs combined.
   Answer: supplier_item-first. For product we will implement a different approach. Search inventory or search stock etc. When searching for items, should resolve to supplier item.
2. **Outlet requirement**: should search default to active outlet only, or search across all outlets the user can access?
    - MVP recommendation: use active outlet if set; otherwise ask which outlet (predictable + safer).
      Answer: Go with recommendation.
3. **Filter UX**: do we require `key:value` tokens, or accept “for Mercato” and “from Fresh Farms” in NLP?
    - MVP recommendation: tokens are ground truth; query-planner LLM handles semantic queries (“meat products with smoked”).
      Answer: Go with recommendation. "for Mercato" and "from Fresh Farms" and other NLP is how queries must be.
4. **Result details**: what’s the minimum “open item” view stakeholders want (inventory, prices, suppliers)?
   Answer: Inventory, prices and suppliers, all are needed.
5. **Parse model choice**: reuse `get_intent_model(settings)` vs introduce `APP_OPENAI_SEARCH_PARSE_MODEL` for tighter cost control.
   Answer: Introduce cost control, let's not overwhelm get_intent_model. get_intent_model is also low cost.
6. **Order-mode pricing rules**:
    - Currency/source of truth (supplier default vs restaurant default vs per-price currency)
    - “Current price” definition (valid_to null vs date windows)
    - If multiple pack sizes/units exist, how do we display unit consistently?
      Answer: supplier default currency, valid_to null, let's display all options.

---

## Risks and mitigations

- **LLM formatting variance**: avoid LLM formatting entirely; search replies are deterministic text. Perhaps, we can restrict replies/responses from LLM to be strict. Instruct it not to suggest unnecessary or extra "helpful" responses.
- **LLM parser drift**: validate JSON, enforce caps, and fall back to simple lexical search when invalid.
- **Ambiguous outlet selection**: require explicit outlet unless active context exists.
- **Performance**: start with `ILIKE %query%` + limit; add indexes/trigram later if needed.
- **Missing pricing data**: in order mode, clearly label “Price unavailable” and allow supplier lookup by name or prompt to upload/update price list.
