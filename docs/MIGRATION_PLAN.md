# Architecture Simplification - Migration Plan

**Branch:** `simplify-deterministic-architecture`
**Goal:** Simplify to clean 3-stage pipeline (ack → planner → presenter) with focus on inventory reconciliation
**Target:** Single-outlet operations with multi-outlet support in schema

---

## 🎯 Success Criteria

- [ ] All existing workflows work (profile, staff, outlets)
- [ ] Test API allows console-mode testing without Telegram
- [ ] Inventory reconciliation tools operational
- [ ] Legacy intent/tool resolver code removed
- [ ] No infinite loops in tool execution
- [ ] LLM costs tracked per call
- [ ] All tests passing

---

## 📊 Current vs. Target Architecture

### Current (Dual System)
```
OLD PATH (intent-driven):
  webhook → intent_classifier → intent_resolver → tool_resolver (loops) → response
                                       ↓
                              item_search_router (fast path)

NEW PATH (deterministic, behind flag):
  webhook → planner → validation → execution → presenter
```

### Target (Clean, Unified)
```
  input → [ack] → planner (with lookup tools) → validation → execution → presenter → output
           ↓         ↓                                         ↓            ↓
        cheap LLM  decision LLM                         deterministic  cheap LLM
                   (max 3 reads)                        (1 write/tool)
```

---

## 🗺️ Phased Migration

### **Phase 0: Preparation & Test Infrastructure** ⚙️
**Goal:** Set up testing infrastructure without breaking existing functionality

**Tasks:**
1. ✅ Create new branch `simplify-deterministic-architecture`
2. Add test API endpoint `/api/v1/test/message`
   - Accept: `{user_id, message, console_mode, restaurant_id?}`
   - Return: `{ack, planner_decision, tool_result, response_text, llm_calls[]}`
   - When `console_mode=true`: log to console, skip Telegram calls
3. Add integration test suite for deterministic flow
4. Document testing workflow in `docs/testing.md`

**Files to create:**
- `app/api/test_routes.py` - Test API endpoint
- `tests/integration/test_deterministic_flow.py` - Integration tests
- `docs/testing.md` - Testing guide

**Testing:**
- Test via API: profile get/update, restaurant list
- Verify console logs show full decision trace
- Confirm no Telegram messages sent in console mode

**Rollback:** Delete test routes, no impact on main flow

---

### **Phase 1: Enable Deterministic Mode by Default** 🔄
**Goal:** Switch all traffic to deterministic system, keep old code as fallback

**Tasks:**
1. Set `DETERMINISTIC_EXECUTION=true` in `.env.example`
2. Update README with new architecture documentation
3. Test all working workflows through test API:
   - Profile operations (get, update name, update phone)
   - Restaurant operations (list, create, update, select)
   - Staff operations (list, invite)
   - Supplier operations (list, create, update, view)
4. Add console logging for planner decisions
5. Monitor LLM costs (should be lower than old system)

**Files to modify:**
- `.env.example` - Set default flag
- `README.md` - Update architecture section
- `app/conversation/processor.py` - Add better logging

**Testing:**
- Run full test suite
- Manual testing of 10 common user scenarios via test API
- Verify all LLM calls recorded in `llm_calls` table
- Check cost reduction vs. old system

**Rollback:** Set `DETERMINISTIC_EXECUTION=false`, revert to old system

---

### **Phase 2: Add Inventory Reconciliation Tools** 📦
**Goal:** Implement core business functionality for inventory management

**Tasks:**
1. Add new tools to `app/ai/deterministic/tool_catalog.py`:
   - `inventory_get_current` - Get current stock for item
   - `inventory_reconcile` - Record actual count, calculate variance
   - `inventory_usage_report` - Usage over time period
   - `supplier_price_compare` - Compare prices across suppliers
2. Implement tool execution in `app/ai/deterministic/execution.py`
3. Add database queries in `app/domain/services/inventory_service.py`
4. Create reconciliation workflow documentation

**Files to create:**
- `app/domain/services/inventory_service.py` - Inventory business logic

**Files to modify:**
- `app/ai/deterministic/tool_catalog.py` - Add 4 new tools
- `app/ai/deterministic/execution.py` - Add execution handlers

**Testing:**
- Upload price list → verify items searchable
- Upload invoice → verify inventory updated
- Get current inventory → verify correct quantities
- Reconcile inventory → verify variance calculated
- Usage report → verify aggregations correct
- Price comparison → verify multi-supplier data

**Rollback:** Remove new tools from catalog, keep old functionality

---

### **Phase 3: Add Lookup Tools & Multi-Step Support** 🔍
**Goal:** Allow planner to call read-only lookup tools to resolve context

**Tasks:**
1. Add `is_read_only` flag to `ToolSpec` in `tool_catalog.py`
2. Implement lookup tools:
   - `lookup_supplier` - Find supplier by fuzzy match
   - `lookup_item` - Find item by fuzzy match
   - `lookup_restaurant` - Find restaurant by fuzzy match
3. Add multi-step execution logic in `execution.py`:
   - Track read-only call count (max 3)
   - Allow sequential read calls
   - Block after first write call
   - Force clarification after 3 reads without resolution
4. Update planner prompt to explain lookup tools

**Files to modify:**
- `app/ai/deterministic/schemas.py` - Add `is_read_only` to ToolSpec
- `app/ai/deterministic/tool_catalog.py` - Add lookup tools
- `app/ai/deterministic/execution.py` - Multi-step logic
- `app/ai/deterministic/planner.py` - Update system prompt

**Testing:**
- Ambiguous queries use lookup tools
- Max 3 lookup calls enforced
- Write calls terminate execution
- Clarification triggered when needed

**Rollback:** Remove lookup tools, keep single-step execution

---

### **Phase 4: Add Ack System** 👋
**Goal:** Improve perceived responsiveness with instant acknowledgment

**Tasks:**
1. Create `app/ai/deterministic/ack.py`
   - Cheap LLM call OR deterministic templates
   - Based on message type (file upload, search, write, etc.)
2. Add ack configuration to settings:
   - `APP_OPENAI_ACK_MODEL` (very cheap model)
   - `ENABLE_ACK` flag (default: true)
3. Integrate into conversation processor
4. Track ack LLM calls separately in telemetry

**Files to create:**
- `app/ai/deterministic/ack.py` - Ack generator

**Files to modify:**
- `app/core/config.py` - Add ack settings
- `app/conversation/processor.py` - Send ack before planner
- `app/ai/model_config.py` - Add `get_ack_model()`

**Testing:**
- Verify ack sent within 100ms
- Confirm ack model is cheapest option
- Test with/without ack enabled

**Rollback:** Set `ENABLE_ACK=false`

---

### **Phase 5: Hybrid Clarification Handling** 🤔
**Goal:** Fast deterministic parsing for simple selections, LLM for complex cases

**Tasks:**
1. Create `app/ai/deterministic/selection_parser.py`:
   - Parse: "1", "2", "10", "first", "second", "last", "next"
   - Parse: "open 2", "supplier 3", "show 1"
   - Handle out-of-range gracefully
2. Add pending selection state to context:
   - `pending_selection: {type, candidates[], created_at}`
   - TTL: 30 minutes (configurable)
3. Update processor to try deterministic parse first, then LLM
4. Add selection state cleanup on successful resolution

**Files to create:**
- `app/ai/deterministic/selection_parser.py` - Deterministic parser

**Files to modify:**
- `app/conversation/context.py` - Add selection state
- `app/conversation/processor.py` - Try parse before LLM

**Testing:**
- Numeric selections work without LLM
- Ordinal selections ("first", "last") work
- Complex responses go to LLM
- Selection state expires after TTL

**Rollback:** Skip deterministic parse, always use LLM

---

### **Phase 6: Remove Legacy Code** 🗑️
**Goal:** Delete old intent/tool resolver system and item search fast path

**Tasks:**
1. Remove `DETERMINISTIC_EXECUTION` flag (always on)
2. Delete legacy files:
   - `app/ai/intent_classifier.py`
   - `app/ai/intent_resolver.py`
   - `app/ai/tool_resolver.py`
   - `app/conversation/item_search_router.py`
   - `app/ai/item_search_query_planner.py`
3. Remove legacy imports from `processor.py`
4. Clean up unused dependencies
5. Update all documentation

**Files to delete:**
- 5 files listed above (~1000 lines)

**Files to modify:**
- `app/conversation/processor.py` - Remove old code paths
- `app/core/config.py` - Remove legacy settings
- `README.md` - Update to reflect new architecture only

**Testing:**
- Full regression test suite
- All workflows still functional
- No import errors
- Documentation accurate

**Rollback:** Revert commits, restore deleted files

---

### **Phase 7: Advanced Search & Optimization** 🚀
**Goal:** Add advanced search capabilities for inventory items

**Tasks:**
1. Add trigram index to `supplier_items.supplier_name_raw`:
   - Alembic migration: `CREATE INDEX idx_supplier_items_name_trgm ON supplier_items USING gin(supplier_name_raw gin_trgm_ops);`
2. Add fuzzy search with similarity scoring
3. Add contextual search (recent suppliers, recent items)
4. Optimize search tool to use indexes
5. Add search result ranking

**Files to create:**
- Alembic migration for trigram index

**Files to modify:**
- `app/domain/services/item_search_service.py` - Add advanced search
- `app/ai/deterministic/execution.py` - Use new search

**Testing:**
- Typos return correct results
- Recent items ranked higher
- Search performance <100ms
- Relevance scoring accurate

**Rollback:** Drop trigram index, use basic search

---

## 🧪 Testing Strategy

### Per-Phase Testing
Each phase includes:
1. Unit tests for new components
2. Integration tests via test API
3. Manual testing checklist
4. LLM cost monitoring
5. Performance benchmarks

### Continuous Testing
- Keep test DB separate from prod
- Run full test suite on each phase
- Monitor Sentry for errors
- Track LLM costs in `llm_calls` table

### Test API Usage
```bash
# Test locally without Telegram
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-uuid",
    "message": "show my profile",
    "console_mode": true
  }'
```

---

## 📈 Success Metrics

### Performance
- [ ] Average response time <2s
- [ ] LLM costs reduced by 30%+ vs. old system
- [ ] 99% of queries resolve in ≤3 tool calls

### Functionality
- [ ] All existing workflows preserved
- [ ] Inventory reconciliation operational
- [ ] Zero infinite loops
- [ ] Clarification rate <20% (user questions clear enough)

### Code Quality
- [ ] ~1000 lines removed
- [ ] Test coverage >80%
- [ ] Zero circular dependencies
- [ ] Documentation up to date

---

## 🚨 Rollback Plan

Each phase is designed to be independently reversible:

1. **Phase 0-2:** Revert commits, feature flags still work
2. **Phase 3-5:** Revert commits, core functionality preserved
3. **Phase 6:** Most risky - requires restore of deleted files
   - Keep deleted files in a `legacy/` branch for 2 weeks
4. **Phase 7:** Drop database index, revert search code

**Emergency rollback:** Set `DETERMINISTIC_EXECUTION=false` in production env

---

## 📅 Estimated Timeline

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 0 | 1 day | None |
| Phase 1 | 1 day | Phase 0 |
| Phase 2 | 2 days | Phase 1 |
| Phase 3 | 2 days | Phase 2 |
| Phase 4 | 1 day | Phase 3 |
| Phase 5 | 1 day | Phase 4 |
| Phase 6 | 1 day | Phase 5 |
| Phase 7 | 2 days | Phase 6 |
| **Total** | **11 days** | Sequential |

With testing and buffer: **~2-3 weeks**

---

## 🎯 Next Steps

1. **Review this plan** - Approve/modify phases
2. **Start Phase 0** - Build test infrastructure
3. **Iterate** - Complete one phase at a time
4. **Test thoroughly** - Use test API for rapid iteration
5. **Monitor** - Track costs, errors, performance

---

## ✅ Decisions Made

1. **Ack model**: LLM (cheap model) - More natural responses
2. **Lookup tool limit**: 3 reads max - Optimal balance of flexibility vs. cost control
3. **Clarification strategy**: Hybrid
   - LLM decides what to clarify
   - Deterministic parser for numeric selections ("1", "2", "first")
   - LLM fallback for complex responses
4. **Search priority**:
   1. Trigram (typo tolerance)
   2. Fuzzy matching
   3. Contextual (recent items ranked higher)
   4. Vector search (Phase 8 - future)
5. **Testing database**: Use existing test/dev DB from `.env`

**Status:** ✅ Approved - Ready to implement Phase 0
