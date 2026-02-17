# Resto Pilot - Product Roadmap

> **Mission:** Help single and multi-outlet restaurants manage inventory, reduce food costs, and streamline operations through intelligent automation.

---

## Table of Contents

1. [Product Vision](#product-vision)
2. [Current State Assessment](#current-state-assessment)
3. [Phased Roadmap](#phased-roadmap)
4. [Critical Missing Features](#critical-missing-features)
5. [Architecture Evolution](#architecture-evolution)
6. [Success Metrics](#success-metrics)

---

## Product Vision

### Target Users
- **Primary:** Single-outlet independent restaurants (1-10 staff)
- **Secondary:** Small chains (2-5 outlets)
- **Future:** Mid-size chains (6-20 outlets)

### Core Value Proposition
**"Know your numbers, reduce waste, save money - through chat"**

Instead of:
- ❌ Spreadsheets that nobody updates
- ❌ Expensive POS systems with complex interfaces
- ❌ Running out of ingredients mid-service
- ❌ Not knowing if you're making or losing money

You get:
- ✅ Always-updated inventory via Telegram
- ✅ Automatic food cost tracking
- ✅ Low-stock alerts before you run out
- ✅ Know your actual profit margins per dish

### Key Insight

**Data entry is a TAX. Insights are VALUE.**

The less typing restaurants do, the more they use it. The more insights they get, the more they need it.

---

## Current State Assessment

### What's Working ✅

1. **Telegram Interface** - Low friction, mobile-first, already on their phones
2. **File Upload** - Invoice/price list OCR reduces manual entry
3. **Deterministic Architecture** - Predictable costs (~$0.006/message)
4. **Basic Database** - Suppliers, items, invoices, inventory batches

### Critical Gaps ❌

1. **No Recipe Management** - Can't calculate food cost per dish
2. **No Consumption Tracking** - Inventory is write-only (purchases in, but no usage out)
3. **No Reporting** - No food cost %, no insights, no alerts
4. **Manual Reconciliation Required** - System doesn't know current stock levels
5. **No Purchase Orders** - Can upload price lists but can't order from them
6. **No Multi-Outlet Support** - Single outlet only

### Architecture Issues

1. **Telegram-Only Limits Complex Operations** - Need web dashboard for bulk entry
2. **LLM Costs Scale Linearly** - Need keyboard buttons for common operations
3. **Missing Background Jobs** - No daily stock calculations, alerts, reports
4. **Incomplete Database Schema** - Missing recipes, stock_levels, purchase_orders, waste logs

---

## Phased Roadmap

### Phase 0: MVP - Inventory Visibility (2 months) 🎯 **CURRENT PRIORITY**

**Goal:** "Always know what I have in stock"

**Success Criteria:**
- 1 restaurant using daily for inventory
- Can answer: "Do I have enough flour for tonight's service?"
- Reduces emergency orders by 50%

**Features:**

| Feature | Status | Priority | Effort |
|---------|--------|----------|--------|
| Upload invoices → create inventory | ✅ Implemented | P0 | Done |
| Set par levels per product | ❌ Not started | P0 | 2 days |
| Daily stock level calculation | ❌ Not started | P0 | 3 days |
| "Running low" alerts (Telegram) | ❌ Not started | P0 | 2 days |
| Manual stock counts (override) | ❌ Not started | P0 | 1 day |
| Basic supplier database | ✅ Implemented | P0 | Done |
| Price tracking (price changes alert) | ❌ Not started | P1 | 2 days |
| Simple inventory report (text) | ❌ Not started | P1 | 1 day |

**Technical Debt to Address:**
- Complete deterministic architecture cleanup (1 week)
- Add PDF hybrid extraction (2 weeks)
- Add background Celery jobs for daily calculations

**Database Schema Additions:**
```sql
-- Stock levels (current view of inventory)
CREATE TABLE stock_levels (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    product_id INTEGER REFERENCES products(id),
    current_quantity DECIMAL(10,3),
    unit VARCHAR(20),
    par_level DECIMAL(10,3),  -- Target stock level
    reorder_point DECIMAL(10,3),  -- Alert when below this
    last_calculated_at TIMESTAMP,
    UNIQUE(restaurant_id, product_id)
);

-- Par level configuration
CREATE TABLE par_level_config (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    product_id INTEGER REFERENCES products(id),
    par_level DECIMAL(10,3),
    reorder_point DECIMAL(10,3),
    notes TEXT,
    UNIQUE(restaurant_id, product_id)
);
```

**Value Delivered:**
- No more running out mid-service
- Reduce emergency orders (expensive, disruptive)
- Know what's in stock without physical count

**Exit Criteria:**
- [ ] 1 restaurant using daily for 1 month
- [ ] Stock alerts working (catches low stock before runout)
- [ ] Invoice upload → stock update working reliably
- [ ] User feedback: "I know what I have at all times"

---

### Phase 1: Operational Workflows (2 months)

**Goal:** "Streamline purchasing and receiving"

**Success Criteria:**
- Create purchase orders in <2 minutes
- Catch delivery discrepancies (ordered 10kg, received 8kg)
- Track waste to identify problem products

**Features:**

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Create purchase orders from price lists | P0 | 1 week | Phase 0 complete |
| Send PO to supplier (email/WhatsApp) | P0 | 3 days | Purchase orders |
| Receive deliveries, verify quantities | P0 | 1 week | Purchase orders |
| Log waste/spoilage | P0 | 3 days | Stock levels |
| Weekly waste report | P1 | 2 days | Waste logging |
| Supplier performance tracking | P1 | 1 week | Receiving logs |
| Delivery verification photo upload | P2 | 3 days | Vision OCR |

**Database Schema Additions:**
```sql
-- Purchase orders
CREATE TABLE purchase_orders (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    po_number VARCHAR(50),
    status VARCHAR(20), -- draft, sent, received, cancelled
    order_date DATE,
    expected_delivery_date DATE,
    total_amount DECIMAL(10,2),
    currency VARCHAR(3),
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP
);

CREATE TABLE purchase_order_items (
    id SERIAL PRIMARY KEY,
    po_id INTEGER REFERENCES purchase_orders(id),
    product_id INTEGER REFERENCES products(id),
    supplier_item_id INTEGER REFERENCES supplier_items(id),
    quantity DECIMAL(10,3),
    unit VARCHAR(20),
    unit_price DECIMAL(10,2),
    total_price DECIMAL(10,2)
);

-- Receiving logs
CREATE TABLE receiving_logs (
    id SERIAL PRIMARY KEY,
    po_id INTEGER REFERENCES purchase_orders(id),
    received_date TIMESTAMP,
    received_by INTEGER REFERENCES users(id),
    notes TEXT
);

CREATE TABLE receiving_items (
    id SERIAL PRIMARY KEY,
    receiving_id INTEGER REFERENCES receiving_logs(id),
    po_item_id INTEGER REFERENCES purchase_order_items(id),
    ordered_quantity DECIMAL(10,3),
    received_quantity DECIMAL(10,3),
    variance DECIMAL(10,3), -- received - ordered
    quality_issue BOOLEAN DEFAULT false,
    notes TEXT
);

-- Waste tracking
CREATE TABLE waste_logs (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    product_id INTEGER REFERENCES products(id),
    quantity DECIMAL(10,3),
    unit VARCHAR(20),
    reason VARCHAR(100), -- spoiled, expired, damaged, overproduction
    cost DECIMAL(10,2),
    logged_by INTEGER REFERENCES users(id),
    logged_at TIMESTAMP,
    notes TEXT
);
```

**Telegram Keyboard Shortcuts:**
```
[📦 Receive Order] [📋 Create PO] [🗑️ Log Waste]
[📊 Stock Levels] [⚠️ Low Stock] [💬 Ask Question]
```

**Value Delivered:**
- Reduce ordering mistakes (forgot to order onions)
- Catch delivery discrepancies (paid for 10kg, got 8kg)
- Identify waste patterns (always throwing away lettuce → order less)

---

### Phase 2: Recipe Costing (2 months)

**Goal:** "Know your actual food cost per dish"

**Success Criteria:**
- Calculate theoretical food cost for all menu items
- Track theoretical vs actual variance
- Identify high-margin items to promote

**Features:**

| Feature | Priority | Effort | Impact |
|---------|----------|--------|--------|
| Define recipes (ingredients + quantities) | P0 | 2 weeks | **HIGH** |
| Auto-calculate recipe cost | P0 | 3 days | **HIGH** |
| Link menu items to recipes | P0 | 1 week | **HIGH** |
| Production tracking (made 50 pizzas) | P0 | 1 week | **HIGH** |
| Theoretical vs actual variance | P0 | 1 week | **HIGH** |
| Menu pricing recommendations | P1 | 3 days | MEDIUM |
| Food cost % reporting | P0 | 1 week | **HIGH** |
| Recipe scaling (double recipe) | P2 | 2 days | LOW |

**Database Schema Additions:**
```sql
-- Recipes
CREATE TABLE recipes (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    name VARCHAR(200),
    category VARCHAR(100), -- appetizer, main, dessert, drink
    description TEXT,
    yield_quantity DECIMAL(10,3),
    yield_unit VARCHAR(20), -- portions, servings, kg, etc
    prep_time_minutes INTEGER,
    cook_time_minutes INTEGER,
    skill_level VARCHAR(20), -- easy, medium, hard
    notes TEXT,
    created_at TIMESTAMP
);

CREATE TABLE recipe_ingredients (
    id SERIAL PRIMARY KEY,
    recipe_id INTEGER REFERENCES recipes(id),
    product_id INTEGER REFERENCES products(id),
    quantity DECIMAL(10,3),
    unit VARCHAR(20),
    preparation_notes TEXT, -- "diced", "julienned", etc
    is_optional BOOLEAN DEFAULT false
);

-- Menu items
CREATE TABLE menu_items (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    name VARCHAR(200),
    category VARCHAR(100),
    recipe_id INTEGER REFERENCES recipes(id), -- can be null for simple items
    sell_price DECIMAL(10,2),
    currency VARCHAR(3),
    is_active BOOLEAN DEFAULT true,
    description TEXT,
    photo_url TEXT
);

-- Production logs (daily prep)
CREATE TABLE production_logs (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    recipe_id INTEGER REFERENCES recipes(id),
    quantity DECIMAL(10,3), -- number of portions made
    production_date DATE,
    produced_by INTEGER REFERENCES users(id),
    notes TEXT,
    created_at TIMESTAMP
);

-- Auto-deduct inventory based on production
CREATE TABLE production_inventory_movements (
    id SERIAL PRIMARY KEY,
    production_id INTEGER REFERENCES production_logs(id),
    product_id INTEGER REFERENCES products(id),
    quantity_used DECIMAL(10,3),
    unit VARCHAR(20),
    theoretical_quantity DECIMAL(10,3), -- from recipe
    variance DECIMAL(10,3) -- actual - theoretical
);
```

**Example User Flow:**
```
User: "Add recipe for Margherita Pizza"
Bot: "What ingredients do you need? (Send a list or photo of recipe card)"

User:
- 200g pizza dough
- 100g tomato sauce
- 150g mozzarella
- 10ml olive oil
- 5g basil

Bot: "Got it! This recipe costs $2.30 per pizza based on current prices.
     Your menu shows Margherita at $12.
     Food cost: 19% ✓ (target: <30%)"

User: "We made 50 pizzas today"
Bot: "Logged production. Deducted from inventory:
     - Dough: 10kg
     - Tomato sauce: 5kg
     - Mozzarella: 7.5kg
     - Olive oil: 500ml
     - Basil: 250g

     Updated stock levels. Mozzarella is running low (2kg left)."
```

**Value Delivered:**
- **Know actual food cost %** (industry standard metric)
- Identify high-margin items to promote
- Catch portion inconsistencies (chef using too much cheese)
- Make data-driven menu pricing decisions

---

### Phase 3: Multi-Outlet + Forecasting (3 months)

**Goal:** "Manage multiple locations, predict needs"

**Features:**

| Feature | Priority | Effort |
|---------|----------|--------|
| Multi-outlet inventory (separate per location) | P0 | 3 weeks |
| Inter-outlet transfers | P0 | 2 weeks |
| Consumption forecasting (run out in X days) | P0 | 3 weeks |
| Auto-suggest reorder quantities | P0 | 1 week |
| Consolidated reporting (all outlets) | P0 | 2 weeks |
| Central purchasing (one PO, multiple outlets) | P1 | 2 weeks |
| Outlet performance comparison | P1 | 1 week |

**Database Schema Additions:**
```sql
-- Inter-outlet transfers
CREATE TABLE transfers (
    id SERIAL PRIMARY KEY,
    from_restaurant_id INTEGER REFERENCES restaurants(id),
    to_restaurant_id INTEGER REFERENCES restaurants(id),
    product_id INTEGER REFERENCES products(id),
    quantity DECIMAL(10,3),
    unit VARCHAR(20),
    transfer_date DATE,
    reason TEXT,
    requested_by INTEGER REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    status VARCHAR(20) -- requested, approved, completed, cancelled
);

-- Consumption forecasts
CREATE TABLE consumption_forecasts (
    id SERIAL PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    product_id INTEGER REFERENCES products(id),
    forecast_date DATE,
    predicted_consumption DECIMAL(10,3),
    current_stock DECIMAL(10,3),
    days_until_runout INTEGER,
    confidence_level DECIMAL(3,2), -- 0.0 to 1.0
    calculated_at TIMESTAMP
);
```

**Forecasting Algorithm (Simple Moving Average to start):**
```python
# Calculate average daily consumption over last 30 days
avg_daily_consumption = sum(last_30_days_usage) / 30

# Predict runout
current_stock = get_current_stock(product_id)
days_until_runout = current_stock / avg_daily_consumption

# Alert if runout in < 3 days
if days_until_runout < 3:
    send_alert(f"⚠️ {product.name} will run out in {days_until_runout:.1f} days")
```

**Value Delivered:**
- Scale to multi-outlet chains
- Reduce emergency orders (predict before runout)
- Balance inventory across outlets (transfer excess)
- Central purchasing (better prices through volume)

---

### Phase 4: Supplier Optimization (3 months)

**Goal:** "Save 5-10% on food costs through smart purchasing"

**Features:**

| Feature | Priority | Effort |
|---------|----------|--------|
| Price comparison across suppliers | P0 | 2 weeks |
| MoQ optimization (bundle to hit minimums) | P0 | 3 weeks |
| Delivery schedule coordination | P0 | 2 weeks |
| Supplier performance scoring | P0 | 2 weeks |
| Recommended supplier switches | P1 | 2 weeks |
| Bulk discount tracking | P1 | 1 week |
| Seasonal price trend analysis | P1 | 2 weeks |

**Supplier Optimization Example:**
```
Your Weekly Order (Naive Approach):
- Supplier A: Tomatoes 10kg ($50), Onions 5kg ($20) = $70
- Supplier B: Basil 500g ($25) = $25
Total: $95 + $30 delivery (2 suppliers) = $125

Optimized Order (AI Recommendation):
- Supplier A: Tomatoes 10kg ($50), Onions 5kg ($20), Basil 500g ($30) = $100
- Saved $5 on basil (bulk discount) + $15 on delivery = $20 saved (16%)
Total: $100 + $15 delivery (1 supplier) = $115

Why:
- Supplier A gives 10% discount on orders >$100
- Consolidate to 1 delivery (save $15)
- Accept slightly higher basil price ($30 vs $25) for overall savings
```

**Value Delivered:**
- **5-10% reduction in food costs** (huge for thin margins)
- Consolidate deliveries (save time, money)
- Leverage volume discounts
- Track supplier reliability (on-time %, quality issues)

---

## Critical Missing Features

### 1. Recipe Management (Phase 2) 🔴 **CRITICAL**

**Why this is #1 priority after Phase 0:**

Every restaurant owner asks: **"Am I making or losing money on this dish?"**

Without recipes, you can't answer this. You know you bought ingredients, you know you sold dishes, but you don't know the connection.

**What's needed:**
- Recipe definition (ingredients + quantities)
- Auto-calculate recipe cost from current prices
- Link menu items to recipes
- Production tracking (made 50 pizzas → deduct inventory)
- Theoretical vs actual variance

**Industry Standard: Food Cost %**
```
Food Cost % = (Food Purchases / Revenue) × 100

Target: 28-32% for full-service restaurants
        25-30% for quick-service

Without recipes, you only know total purchases, not cost per dish.
```

### 2. Stock Level Calculation (Phase 0) 🔴 **CRITICAL**

**Current problem:** Inventory is write-only.

Invoices create inventory batches, but nothing depletes them. The system doesn't know current stock.

**What's needed:**
```sql
-- Daily background job
UPDATE stock_levels
SET current_quantity = (
    opening_inventory
    + purchases
    - production_usage
    - waste
    - transfers_out
    + transfers_in
)
```

**With this, you can:**
- Answer "Do I have 5kg flour?" instantly
- Send alerts when low
- Calculate theoretical vs actual (catch theft/waste)

### 3. Web Dashboard (Phase 1-2) 🟡 **HIGH PRIORITY**

**Telegram is great for:**
- ✅ Quick updates ("Add 5kg flour")
- ✅ Alerts ("Running low on eggs")
- ✅ Mobile queries ("Who supplies onions?")

**Telegram is terrible for:**
- ❌ Defining recipes (15 ingredients with quantities)
- ❌ Viewing reports (monthly P&L, food cost trends)
- ❌ Bulk operations (weekly inventory count of 200 items)
- ❌ Comparing suppliers (table with 10 suppliers × 20 products)

**Solution:** Web dashboard for complex operations

**Tech Stack Recommendation:**
- **Frontend:** Next.js + React + Tailwind CSS
- **Backend:** Same FastAPI backend, add web routes
- **Auth:** Telegram WebApp authentication (seamless login)
- **Deployment:** Vercel (frontend) + existing backend

### 4. Background Jobs (Phase 0) 🔴 **CRITICAL**

**Current:** System only reacts to user input

**Needed:** Proactive background jobs

```python
# app/workers/background_jobs.py

@celery.task
def daily_stock_calculation():
    """Recalculate stock levels for all restaurants"""
    for restaurant in Restaurant.all():
        calculate_stock_levels(restaurant)
        check_par_levels(restaurant)
        generate_reorder_suggestions(restaurant)

@celery.task
def daily_low_stock_alerts():
    """Send Telegram alerts for low stock"""
    for restaurant in Restaurant.all():
        low_stock_items = get_items_below_par(restaurant)
        if low_stock_items:
            send_telegram_message(
                restaurant.owner.telegram_id,
                f"⚠️ Running low:\n" + format_low_stock_list(low_stock_items)
            )

@celery.task
def weekly_consumption_analysis():
    """Analyze consumption patterns, update forecasts"""
    for restaurant in Restaurant.all():
        update_consumption_forecasts(restaurant)
        detect_usage_anomalies(restaurant)

@celery.task
def monthly_food_cost_report():
    """Generate food cost % report"""
    for restaurant in Restaurant.all():
        report = generate_food_cost_report(restaurant)
        send_telegram_message(
            restaurant.owner.telegram_id,
            format_monthly_report(report)
        )
```

**Schedule (using Celery Beat):**
```python
# app/workers/celery_beat_schedule.py

CELERY_BEAT_SCHEDULE = {
    'daily-stock-calculation': {
        'task': 'app.workers.background_jobs.daily_stock_calculation',
        'schedule': crontab(hour=2, minute=0),  # 2 AM daily
    },
    'daily-low-stock-alerts': {
        'task': 'app.workers.background_jobs.daily_low_stock_alerts',
        'schedule': crontab(hour=8, minute=0),  # 8 AM daily
    },
    'weekly-consumption-analysis': {
        'task': 'app.workers.background_jobs.weekly_consumption_analysis',
        'schedule': crontab(day_of_week=1, hour=3, minute=0),  # Monday 3 AM
    },
    'monthly-food-cost-report': {
        'task': 'app.workers.background_jobs.monthly_food_cost_report',
        'schedule': crontab(day_of_month=1, hour=9, minute=0),  # 1st of month, 9 AM
    },
}
```

### 5. Consumption Tracking (Phase 2) 🟡 **HIGH PRIORITY**

**Current:** No way to track usage (how inventory gets depleted)

**Methods to track consumption:**

1. **Recipe-based (most accurate):**
   ```
   Made 50 pizzas → each uses 200g dough, 100g sauce, 150g cheese
   Total: 10kg dough, 5kg sauce, 7.5kg cheese deducted
   ```

2. **Sales-based (requires POS integration):**
   ```
   Sold 50 pizzas → look up recipe → deduct ingredients
   ```

3. **Manual (least accurate but better than nothing):**
   ```
   User: "Used 10kg flour today"
   Bot: "Logged. Flour stock: 15kg → 5kg"
   ```

4. **Periodic counts (catch-all for variance):**
   ```
   Weekly: Physical count shows 50kg flour
   System shows 60kg theoretical
   Variance: -10kg (10kg unaccounted - waste? theft? measurement error?)
   ```

### 6. Expiry Tracking (Phase 1) 🟡 **MEDIUM PRIORITY**

**Why it matters:** Food waste from expired products is 5-10% of food costs

**What's needed:**
```sql
CREATE TABLE inventory_batches (
    id SERIAL PRIMARY KEY,
    product_id INTEGER,
    quantity DECIMAL(10,3),
    expiry_date DATE,  -- Add this
    received_date DATE,
    lot_number VARCHAR(50),
    status VARCHAR(20) -- fresh, expiring_soon, expired
);

-- Daily job
@celery.task
def check_expiring_products():
    expiring_soon = get_products_expiring_in_days(3)
    send_alert(f"⚠️ Expiring in 3 days: {format_list(expiring_soon)}")
```

**FIFO (First In, First Out) enforcement:**
```python
def deduct_inventory(product_id, quantity):
    """Always deduct from oldest batch first"""
    batches = InventoryBatch.filter(product_id=product_id).order_by('received_date')
    remaining = quantity
    for batch in batches:
        if batch.quantity >= remaining:
            batch.quantity -= remaining
            break
        else:
            remaining -= batch.quantity
            batch.quantity = 0
```

---

## Architecture Evolution

### Current Architecture (Phase 0)

```
┌─────────────────────────────────────────┐
│         Telegram Bot (Primary)          │
│                                         │
│  - Chat interface                       │
│  - File uploads                         │
│  - Notifications                        │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│         FastAPI Backend                 │
│                                         │
│  Deterministic Flow:                    │
│  ACK → Planner → Execution → Presenter  │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│         PostgreSQL Database             │
│                                         │
│  - restaurants, suppliers, products     │
│  - invoices, inventory_batches          │
│  - supplier_items, supplier_prices      │
└─────────────────────────────────────────┘
```

### Target Architecture (Phase 2-3)

```
┌──────────────────────┐    ┌──────────────────────┐
│   Telegram Bot       │    │   Web Dashboard      │
│                      │    │                      │
│  - Quick updates     │    │  - Bulk operations   │
│  - Alerts            │    │  - Reporting         │
│  - File uploads      │    │  - Recipe management │
│  - Mobile queries    │    │  - Analytics         │
└──────────┬───────────┘    └──────────┬───────────┘
           │                           │
           └──────────┬────────────────┘
                      │
                      ▼
           ┌────────────────────────┐
           │   FastAPI Backend      │
           │                        │
           │  - REST API            │
           │  - WebSocket (alerts)  │
           │  - Celery workers      │
           │  - Background jobs     │
           └────────┬───────────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
    ┌──────────┬──────────┬──────────┐
    │PostgreSQL│  Redis   │  S3      │
    │          │          │          │
    │ Main DB  │ Cache    │ Files    │
    └──────────┴──────────┴──────────┘
```

### Key Architectural Decisions

#### 1. Telegram + Web (Not Telegram-Only)

**Rationale:**
- Telegram: Low friction for quick operations (80% of interactions)
- Web: Complex operations that need rich UI (20% of interactions)

**Implementation:**
- Shared backend API
- Telegram WebApp for seamless SSO (no separate login)
- Same database, same business logic

#### 2. Reduce LLM Usage Through UI Patterns

**Current:** Every message uses LLM planner (~$0.003/message)

**Optimization:** Telegram keyboard buttons for common operations

```python
# app/telegram/keyboards.py

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [
            KeyboardButton("📦 Stock Levels"),
            KeyboardButton("📋 Create Order"),
        ],
        [
            KeyboardButton("🗑️ Log Waste"),
            KeyboardButton("📊 Reports"),
        ],
        [
            KeyboardButton("🔍 Search Suppliers"),
            KeyboardButton("💬 Ask Question"),  # Only this uses LLM
        ]
    ])
```

**When user presses button:**
- Skip planner (deterministic routing)
- Show inline keyboard for next step
- Only use LLM for final natural language formatting

**Savings:** 70% reduction in LLM calls = ~$0.001/message

#### 3. Background Jobs for Proactive Value

**Philosophy:** Don't make users ask for everything

**Examples:**
- 🔴 Bad: User asks "What's my food cost?" → LLM calculates → responds
- 🟢 Good: System calculates food cost daily, sends report automatically

**Implementation:** Celery Beat (scheduled tasks)

```python
# Runs daily at 8 AM
@celery.task
def morning_briefing():
    """Send daily summary to restaurant owners"""
    for restaurant in Restaurant.all():
        summary = {
            'low_stock_items': get_items_below_par(restaurant),
            'expiring_soon': get_expiring_in_days(restaurant, 2),
            'pending_orders': get_pending_orders(restaurant),
            'yesterdays_waste': get_waste_summary(restaurant, yesterday),
        }
        send_telegram_message(
            restaurant.owner.telegram_id,
            format_morning_briefing(summary)
        )
```

#### 4. Event-Driven Stock Calculations

**Current approach (wrong):**
```python
# Recalculate stock every time someone asks
def get_current_stock(product_id):
    opening = get_opening_inventory(product_id)
    purchases = sum_purchases(product_id)
    usage = sum_usage(product_id)
    waste = sum_waste(product_id)
    return opening + purchases - usage - waste
```

**Better approach:**
```python
# Maintain stock_levels table, update on events
def on_invoice_received(invoice):
    for item in invoice.items:
        StockLevel.increment(item.product_id, item.quantity)

def on_production_logged(production):
    recipe = production.recipe
    for ingredient in recipe.ingredients:
        StockLevel.decrement(ingredient.product_id, ingredient.quantity * production.quantity)

def on_waste_logged(waste):
    StockLevel.decrement(waste.product_id, waste.quantity)

# Query is instant
def get_current_stock(product_id):
    return StockLevel.get(product_id).current_quantity
```

#### 5. Progressive Enhancement (Not Big Bang)

**Wrong approach:**
- Build everything for 6 months
- Launch with 50 features
- Nobody uses 40 of them

**Right approach:**
- Launch Phase 0 with 5 core features in 2 months
- Get 1-5 restaurants using it daily
- Learn what they actually need
- Build Phase 1 based on real usage data
- Repeat

**Why this matters:**
- You have 1 restaurant ready to use this NOW
- They'll tell you what's missing
- Better to have 5 features that work perfectly than 50 half-baked ones

---

## Success Metrics

### Phase 0: Inventory Visibility

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Daily active users | 1 restaurant using daily | Login/message count |
| Emergency orders reduced | -50% | User survey (before/after) |
| Stock accuracy | >90% | Physical count vs system count |
| Time to check stock | <30 seconds | "Do I have flour?" → answer |
| Low stock alerts caught | >80% of runouts predicted | Alert vs actual runout |

**User feedback to validate:**
- "I always know what's in stock"
- "I haven't run out mid-service since using this"
- "Checking inventory takes seconds, not minutes"

### Phase 1: Operational Workflows

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Purchase orders created/week | 3+ per restaurant | PO count |
| Delivery discrepancies caught | 5+ per month | Receiving variance logs |
| Waste tracked | 100% (not ignored) | Waste log entries |
| Time to create PO | <2 minutes | Time from "Create PO" to sent |

**User feedback to validate:**
- "Ordering is faster and more accurate"
- "I catch supplier mistakes now"
- "I know exactly how much I'm wasting"

### Phase 2: Recipe Costing

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Menu items with recipes | 100% | Recipe coverage |
| Food cost % accuracy | ±2% | Theoretical vs actual |
| Menu price adjustments | 2+ per month | Price changes based on cost data |
| High-margin items identified | Top 5 | Margin ranking |

**User feedback to validate:**
- "I know my actual food cost now"
- "I can make data-driven pricing decisions"
- "I caught portion inconsistencies (chef using too much)"

### Phase 3-4: Multi-Outlet + Optimization

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Food cost reduction | 5-10% | Before/after comparison |
| Emergency orders reduced | -80% | Order frequency |
| Supplier consolidation | -30% delivery fees | Delivery cost before/after |

---

## Immediate Next Steps (This Week)

### 1. Complete Current Technical Debt ✅

**From implementation plan:**
- Remove duplicate ACK implementation
- Simplify session management
- Add unit tests for ack, clarifier, presenter
- Update documentation

**Why first:** Clean foundation before building new features

**Effort:** 1 week

### 2. Add Phase 0 Core Features 🎯

**Priority order:**
1. Stock levels table + calculation logic (3 days)
2. Par level configuration (2 days)
3. Low stock alerts via Telegram (2 days)
4. Manual stock count override (1 day)
5. Price change alerts (2 days)

**Why this order:**
- Stock levels unlock everything else
- Par levels enable alerts
- Alerts deliver immediate value
- Manual counts catch errors
- Price alerts show system is watching

**Effort:** 2 weeks

### 3. Onboard First Restaurant 🎯

**Week 3-4: User onboarding**
1. Set up their account
2. Import their suppliers (manual or price list upload)
3. Set par levels for top 20 products
4. Upload 1 week of invoices
5. Let system calculate stock
6. Monitor alerts for 1 week

**Success criteria:**
- They use it daily for 2 weeks
- They catch at least 1 low-stock situation
- They give feedback on what's missing

**Then:** Iterate on Phase 0 based on their feedback before building Phase 1

---

## Open Questions

### Technical

1. **Should we use OpenRouter or direct OpenAI API?**
   - Current: OpenRouter (flexibility to switch models)
   - Tradeoff: 10% markup vs model flexibility
   - Decision needed: Phase 0 completion

2. **How to handle multi-currency?**
   - Current: currency VARCHAR(3) per item
   - Problem: How to compare prices across currencies?
   - Options: Store in base currency + exchange rate table
   - Decision needed: Before Phase 1 (supplier optimization)

3. **Web dashboard framework?**
   - Options: Next.js (full-stack) vs React SPA + FastAPI
   - Recommendation: Next.js (better DX, can self-host or Vercel)
   - Decision needed: Before Phase 1 starts

### Product

1. **Freemium or paid-only?**
   - Option A: Free for single outlet, $49/month for multi-outlet
   - Option B: 30-day free trial, then $29/month per outlet
   - Option C: Usage-based ($0.05/message after 100 free/month)
   - Decision needed: Before 10 restaurants (need business model clarity)

2. **POS integration or standalone?**
   - Standalone: Simpler, works for any restaurant
   - Integrated: Auto-track sales → consumption
   - Start: Standalone, add POS integration Phase 3+
   - Decision needed: Before Phase 2 (recipe costing)

3. **B2B (sell to restaurants) or B2B2C (white-label for suppliers)?**
   - B2B: Sell direct to restaurants
   - B2B2C: Suppliers white-label it for their customers
   - Start: B2B, explore B2B2C Phase 4+
   - Decision needed: After 50 restaurants (need PMF first)

---

## Appendix: Competitive Landscape

### Direct Competitors

1. **MarketMan** - $200-500/month, complex, feature-rich
   - Strength: Mature product, lots of features
   - Weakness: Expensive, complex setup, desktop-first

2. **Typsy / BlueCart** - $99-200/month, procurement-focused
   - Strength: Good supplier marketplace
   - Weakness: Weak inventory tracking

3. **SimpleOrder** - Free/Freemium, basic ordering
   - Strength: Free tier attracts users
   - Weakness: No recipe costing, no reporting

### Our Advantage

1. **Telegram-first** - Mobile-native, no app install
2. **AI-powered** - Natural language, file upload OCR
3. **Simple pricing** - $29-49/month (vs $200+)
4. **Progressive enhancement** - Start simple, grow with them

### What We Can't Compete On (Yet)

1. Feature breadth (they have 10 years of features)
2. Integrations (POS, accounting, etc.)
3. Supplier marketplace (pre-negotiated prices)

### What We Win On

1. **Ease of use** - 5 minutes to start vs 2 days setup
2. **Mobile-first** - Telegram beats their mobile apps
3. **Price** - 70% cheaper for solo restaurants
4. **AI assistance** - They have forms, we have conversation

---

**Last Updated:** 2026-02-17
**Current Phase:** Phase 0 (Inventory Visibility)
**Next Milestone:** Onboard 1 restaurant, daily usage for 1 month
