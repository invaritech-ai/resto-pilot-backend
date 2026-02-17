# Phase 0 Cleanup Plan - Strip to Essentials

> **Goal:** Reduce backend to absolute minimum needed for Phase 0 (upload invoices → stock tracking → alerts)

**Estimated Impact:**
- 🗑️ Delete: ~2,500 lines of code
- 📉 Database tables: 31 → 18 (42% reduction)
- 💰 LLM costs: 40% reduction
- 🎯 Focus: 100% on core value

---

## Quick Stats

| Metric | Before | After | Reduction |
|--------|--------|-------|-----------|
| Database tables | 31 | 18 | 42% |
| API routes | 20+ | 5 | 75% |
| Tool definitions | 27 | 8 | 70% |
| Python files | 197 | ~150 | 24% |
| LLM calls per message | 3-4 | 1-2 | 40% |

---

## Cleanup Checklist

### Phase 1: Delete Unused Feature Files (30 min)

#### Staff Management System ❌
```bash
# Delete files
rm app/ai/db_tools/staff.py
rm app/ai/db_tools/invites.py

# Verify no imports remain
grep -r "from app.ai.db_tools import staff" app/
grep -r "from app.ai.db_tools import invites" app/
# Should return empty
```

**Why:** Phase 0 is single-user per restaurant. Staff/invites are Phase 2+.

#### Item Search System ❌
```bash
# Already deleted in git, clean up references
grep -r "item_search" app/ | grep -v ".pyc" | grep -v "__pycache__"
# Remove any remaining imports/references
```

**Why:** Phase 0 is invoice → inventory. Search optimization is Phase 1+.

#### Deterministic LLM Modules ❌
```bash
# Remove LLM fallback modules (over-engineered)
rm app/ai/deterministic/clarifier.py
rm app/ai/deterministic/planner.py
rm app/ai/deterministic/presenter.py

# Keep only
# app/ai/deterministic/execution.py - tool executor
# app/ai/deterministic/schemas.py - dataclasses
# app/ai/deterministic/tool_catalog.py - tool definitions
# app/ai/deterministic/validation.py - validation
```

**Why:** These add LLM costs and latency. Phase 0 uses simple deterministic execution.

#### User Management API ❌
```bash
# Delete entire file
rm app/api/v1/routes/users.py

# Update router registration in app/api/v1/__init__.py
# Remove: app.include_router(users.router, prefix="/users", tags=["users"])
```

**Why:** Telegram auth only, no user CRUD needed.

---

### Phase 2: Database Cleanup (1 hour)

#### Create Migration to Drop Tables

```bash
# Generate migration
alembic revision -m "phase_0_cleanup_drop_unused_tables"
```

**Edit the migration file:**

```python
# alembic/versions/XXXXX_phase_0_cleanup_drop_unused_tables.py

def upgrade():
    # Drop unused tables
    op.drop_table('invite_codes')
    op.drop_table('product_aliases')
    op.drop_table('price_comparisons')
    op.drop_table('supplier_prices')
    op.drop_table('supplier_disputes')
    op.drop_table('supplier_item_products')  # Simplify to 1:1 FK instead
    op.drop_table('file_processing_page_jobs')
    op.drop_table('file_processing_steps')
    op.drop_table('file_processing_payloads')
    op.drop_table('processing_events')  # Duplicate of llm_calls

    # Simplify user table
    op.drop_column('users', 'is_phone_verified')
    op.drop_column('users', 'state')
    op.drop_column('users', 'state_data')

    # Simplify restaurant table
    op.drop_column('restaurants', 'onboarding_status')
    op.drop_column('restaurants', 'internal_name')
    op.drop_column('restaurants', 'address')

    # Simplify suppliers table
    op.drop_column('suppliers', 'lead_time_days')
    op.drop_column('suppliers', 'language')
    op.drop_column('suppliers', 'contact_email')
    op.drop_column('suppliers', 'contact_phone')
    op.drop_column('suppliers', 'name_normalized')

    # Simplify inventory_batches table
    op.drop_column('inventory_batches', 'location_id')
    op.drop_column('inventory_batches', 'expiry_date')
    op.drop_column('inventory_batches', 'status')

    # Simplify restaurant_users (drop role-based permissions)
    op.drop_column('restaurant_users', 'role')
    op.drop_column('restaurant_users', 'status')
    op.drop_column('restaurant_users', 'permissions')
    op.add_column('restaurant_users', sa.Column('is_active', sa.Boolean, default=True))

    # Simplify telegram_sessions
    op.drop_column('telegram_sessions', 'hint_command')
    op.drop_column('telegram_sessions', 'flush_at')

def downgrade():
    # Recreate tables if needed (or leave empty for one-way cleanup)
    pass
```

**Run migration:**
```bash
alembic upgrade head
```

**Verify:**
```bash
psql -d resto_pilot -c "\dt"  # List all tables, verify dropped
```

---

### Phase 3: Simplify Tool Catalog (45 min)

**File:** `app/ai/deterministic/tool_catalog.py`

**Remove these tool definitions (~20 tools):**

```python
# DELETE - Staff management tools
"staff_list"
"staff_revoke_access"

# DELETE - Invite management tools
"invite_codes_create"
"invite_codes_list"
"invite_codes_delete"

# DELETE - Item search tools (Phase 1+)
"supplier_items_search"  # Keep basic list, remove search
"items_search_across_suppliers"

# DELETE - Profile tools (not core)
"profile_get"
"profile_update"

# DELETE - Advanced restaurant tools
"restaurants_create"  # Just use telegram registration
"restaurants_update"  # Not needed for Phase 0

# DELETE - Supplier management (keep only link)
"suppliers_list"  # Can query directly
"suppliers_create"  # Create via file upload only
```

**KEEP ONLY (8 tools):**

```python
# File processing (core Phase 0)
"files_process_invoice"
"files_process_price_list"
"files_confirm_processing"
"files_cancel_processing"

# Inventory (core Phase 0)
"inventory_set_par_level"
"inventory_check_stock"
"inventory_manual_count"

# Supplier (minimal)
"suppliers_link_to_restaurant"
```

**After cleanup, tool_catalog.py should be ~150 lines (down from ~350)**

---

### Phase 4: Simplify Execution.py (1 hour)

**File:** `app/ai/deterministic/execution.py`

**Remove these handler functions:**

```python
# DELETE - Staff handlers
def _handle_staff_list(...)
def _handle_staff_revoke_access(...)

# DELETE - Invite handlers
def _handle_invite_codes_create(...)
def _handle_invite_codes_list(...)
def _handle_invite_codes_delete(...)

# DELETE - Search handlers (Phase 1+)
def _handle_supplier_items_search(...)
def _handle_items_search_across_suppliers(...)

# DELETE - Profile handlers
def _handle_profile_get(...)
def _handle_profile_update(...)

# DELETE - Complex restaurant handlers
def _handle_restaurants_create(...)
def _handle_restaurants_update(...)
```

**KEEP ONLY:**

```python
# Core Phase 0 handlers
def _handle_files_process_invoice(...)
def _handle_files_process_price_list(...)
def _handle_files_confirm_processing(...)
def _handle_inventory_set_par_level(...)  # ADD NEW
def _handle_inventory_check_stock(...)     # ADD NEW
def _handle_inventory_manual_count(...)    # ADD NEW
def _handle_suppliers_link_to_restaurant(...)
```

**After cleanup: ~400 lines (down from ~839)**

---

### Phase 5: Clean Up Processor.py (30 min)

**File:** `app/conversation/processor.py`

**Remove these functions:**

```python
# Lines 43-50: DELETE - Item search regex patterns
_FOLLOWUP_RE = re.compile(...)
_SHOW_AGAIN_RE = re.compile(...)

# Lines 53-59: DELETE
def _extract_followup_query(...):

# Lines 62-68: DELETE
def _is_show_again_request(...):

# Lines 71-82: DELETE
def _last_list_is_fresh(...):

# Lines 110-122: DELETE (unused)
def _detect_file_type_from_text(...):

# Lines 125-134: DELETE (unused)
def _is_derive_from_file(...):

# Lines 137-160: DELETE (complex matching not used)
def _match_restaurant_from_message(...):

# Lines 253-346: DELETE (duplicate ACK)
def _generate_ack_text(...):
```

**After cleanup: ~1,200 lines (down from ~1,500)**

---

### Phase 6: Simplify API Routes (30 min)

#### A. Delete User Routes
**File:** `app/api/v1/routes/users.py`
```bash
rm app/api/v1/routes/users.py
```

**Update:** `app/api/v1/__init__.py`
```python
# REMOVE this line:
# app.include_router(users.router, prefix="/users", tags=["users"])
```

#### B. Simplify Restaurant Routes
**File:** `app/api/v1/routes/restaurants.py`

**Remove these endpoints:**
```python
# DELETE - Staff management endpoints
@router.get("/{restaurant_id}/members")
@router.delete("/{restaurant_id}/members/{user_id}")

# DELETE - Invite management endpoints
@router.post("/{restaurant_id}/invites")
@router.get("/{restaurant_id}/invites")
@router.delete("/{restaurant_id}/invites/{invite_code}")
```

**Keep only:**
```python
@router.get("/")  # List user's restaurants
@router.post("/")  # Create restaurant (via telegram registration)
```

#### C. Simplify Auth Routes
**File:** `app/api/v1/routes/auth.py`

**Remove:**
```python
@router.post("/telegram-webapp")  # Webapp auth not needed
```

**Keep:**
```python
# Basic session handling only (if needed)
```

---

### Phase 7: Remove Database Models (15 min)

**Delete these model files:**

```bash
# Delete unused models
rm app/db/models/invite_codes.py
rm app/db/models/product_aliases.py
rm app/db/models/price_comparisons.py
rm app/db/models/supplier_prices.py
rm app/db/models/supplier_disputes.py
rm app/db/models/supplier_item_products.py
rm app/db/models/processing_events.py
```

**Update:** `app/db/models/__init__.py`
```python
# Remove imports for deleted models
```

**Simplify remaining models:**
- `app/db/models/user.py` - Remove: is_phone_verified, state, state_data columns
- `app/db/models/restaurant.py` - Remove: onboarding_status, internal_name, address columns
- `app/db/models/suppliers.py` - Remove: lead_time_days, language, contact_email, contact_phone, name_normalized columns
- `app/db/models/inventory_batches.py` - Remove: location_id, expiry_date, status columns

---

### Phase 8: Clean Up DB Tools (20 min)

**File:** `app/ai/db_tools/base.py`

**Remove from `create_db_tools()` function:**

```python
# DELETE these imports
from .staff import create_staff_tools
from .invites import create_invite_tools

# DELETE these function calls
tools.extend(create_staff_tools())
tools.extend(create_invite_tools())
```

**Keep only:**
```python
from .file_processing import create_file_processing_tools
from .suppliers import create_supplier_tools
from .inventory import create_inventory_tools  # ADD NEW

tools.extend(create_file_processing_tools())
tools.extend(create_supplier_tools())
tools.extend(create_inventory_tools())  # ADD NEW
```

---

### Phase 9: Simplify Services (30 min)

#### Delete ItemSearchService
```bash
rm app/domain/services/item_search_service.py
```

#### Delete InviteService (if exists)
```bash
rm app/domain/services/invite_service.py
```

#### Simplify RestaurantService
**File:** `app/domain/services/restaurant_service.py`

**Remove methods:**
```python
def create_invite_code(...)  # DELETE
def list_invite_codes(...)   # DELETE
def delete_invite_code(...)  # DELETE
def list_members(...)        # DELETE
def revoke_access(...)       # DELETE
```

**Keep methods:**
```python
def get_by_id(...)
def get_user_restaurants(...)
def create(...)
def update(...)  # Simplified
```

---

### Phase 10: Clean Up Responses (15 min)

**File:** `app/conversation/responses.py`

**Remove these response formatters:**

```python
# DELETE - Staff responses
def format_staff_list_response(...)
def format_staff_revoked_response(...)

# DELETE - Invite responses
def format_invite_created_response(...)
def format_invite_list_response(...)
def format_invite_deleted_response(...)

# DELETE - Search responses (if complex)
def format_search_results_response(...)
```

**Keep only:**
```python
def format_invoice_uploaded_response(...)
def format_price_list_uploaded_response(...)
def format_stock_check_response(...)  # ADD NEW
def format_low_stock_alert(...)        # ADD NEW
def format_par_level_set_response(...) # ADD NEW
```

---

## Verification Checklist

After cleanup, verify:

### ✅ Code Quality
```bash
# No broken imports
python -c "from app.main import app"

# All tests pass
pytest tests/ -v

# No unused imports
ruff check app/ --select F401

# Type checking passes (if using mypy)
mypy app/
```

### ✅ Database
```bash
# Tables exist
psql -d resto_pilot -c "\dt" | grep -E "users|restaurants|suppliers|products|invoices|inventory"

# Dropped tables gone
psql -d resto_pilot -c "\dt" | grep -E "invite_codes|product_aliases|price_comparisons"
# Should return empty
```

### ✅ API
```bash
# Start server
uvicorn app.main:app --reload

# Test endpoints
curl -X POST http://localhost:8000/api/v1/telegram -H "Content-Type: application/json" -d '{}'
# Should return 401 (auth) not 404

# Verify deleted endpoints return 404
curl -X GET http://localhost:8000/api/v1/users
# Should return 404
```

### ✅ Minimal Surface Area
```bash
# Count Python files
find app/ -name "*.py" | wc -l
# Should be ~150 (down from ~197)

# Count database tables
psql -d resto_pilot -c "\dt" | wc -l
# Should be ~18 (down from ~31)

# Count tool definitions
grep "def.*_tool" app/ai/deterministic/tool_catalog.py | wc -l
# Should be ~8 (down from ~27)
```

---

## Before & After Comparison

### Database Schema

**BEFORE (31 tables):**
```
✅ Core
- users, restaurants, restaurant_users
- suppliers, products, supplier_items
- invoices, invoice_line_items
- inventory_batches, inventory_movements
- documents

⚠️ Over-engineered
- invite_codes (DELETE)
- product_aliases (DELETE)
- price_comparisons (DELETE)
- supplier_prices (DELETE)
- supplier_disputes (DELETE)
- supplier_item_products (DELETE)
- file_processing_page_jobs (DELETE)
- file_processing_steps (DELETE)
- file_processing_payloads (DELETE)
- processing_events (DELETE)

✅ Keep
- file_processing_runs
- file_processing_staging
- telegram_messages
- telegram_outgoing_messages
- telegram_sessions
- llm_calls
```

**AFTER (18 tables):**
```
Core Phase 0:
- users (simplified)
- restaurants (simplified)
- restaurant_users (simplified)
- suppliers (simplified)
- products
- supplier_items
- invoices
- invoice_line_items
- inventory_batches (simplified)
- inventory_locations
- inventory_movements
- documents

Processing:
- file_processing_runs
- file_processing_staging

Telemetry:
- telegram_messages
- telegram_outgoing_messages
- telegram_sessions (simplified)
- llm_calls
```

### API Endpoints

**BEFORE (20+ endpoints):**
```
❌ DELETE:
GET    /users
POST   /users
GET    /restaurants/{id}/members
POST   /restaurants/{id}/invites
GET    /restaurants/{id}/invites
DELETE /restaurants/{id}/invites/{code}
POST   /auth/telegram-webapp
... (10+ more)
```

**AFTER (5 endpoints):**
```
✅ KEEP:
POST   /telegram                           # Webhook
POST   /test/message                        # Test API
POST   /files/upload                        # File processing
GET    /files/{run_id}                      # Check status
POST   /suppliers/{id}/restaurants/{id}    # Link supplier
```

### Tool Catalog

**BEFORE (27 tools):**
```
File Processing: 4
Inventory: 0 (missing!)
Staff: 2
Invites: 3
Profile: 2
Restaurants: 3
Suppliers: 5
Item Search: 8
```

**AFTER (8 tools):**
```
File Processing: 4
Inventory: 3 (NEW!)
Suppliers: 1
```

---

## Estimated Time

| Task | Time | Risk |
|------|------|------|
| Delete files | 30 min | Low |
| Database migration | 1 hour | Medium |
| Simplify tool_catalog.py | 45 min | Low |
| Simplify execution.py | 1 hour | Medium |
| Clean up processor.py | 30 min | Low |
| Simplify API routes | 30 min | Low |
| Remove models | 15 min | Low |
| Clean up db_tools | 20 min | Low |
| Simplify services | 30 min | Low |
| Clean up responses | 15 min | Low |
| **TOTAL** | **~6 hours** | **Medium** |

---

## Rollback Plan

If cleanup breaks something critical:

1. **Git revert:**
   ```bash
   git checkout main
   git branch cleanup-rollback
   git reset --hard HEAD~1  # Undo last commit
   ```

2. **Database rollback:**
   ```bash
   alembic downgrade -1  # Undo last migration
   ```

3. **Restore from backup:**
   ```bash
   psql -d resto_pilot < backup_before_cleanup.sql
   ```

---

## Success Criteria

✅ All tests passing
✅ Telegram bot works (send message → get response)
✅ File upload works (invoice → inventory created)
✅ Database migration successful
✅ No broken imports
✅ ~40% fewer database tables
✅ ~70% fewer API routes
✅ ~40% reduction in LLM costs

---

## Next Steps After Cleanup

Once cleanup is complete:

1. **Add Phase 0 Core Features:**
   - Stock levels calculation
   - Par level configuration
   - Low stock alerts
   - Background jobs

2. **Test with Real Restaurant:**
   - Upload 1 week of invoices
   - Set par levels
   - Verify alerts work

3. **Iterate Based on Feedback:**
   - What's missing?
   - What's confusing?
   - What takes too long?

---

**Let's strip this down to the essentials and build back up from a solid foundation!** 🚀
