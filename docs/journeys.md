# User journeys (v1 happy paths)

This doc defines the **happy-path user journeys** we want to support in v1.

## Personas (v1 priority)

1. **Owner / operator** (primary)
2. **Outlet staff** (secondary)

## Platform constraints (important)

**Note**: This document was written for the old capability-based architecture. The bot now uses a general-purpose agent with tool-calling and policy enforcement. Any references to "capability slugs" below are historical; interpret them as tool availability + role/scope policy checks.

Current architecture:

-   The bot uses a general-purpose agent loop with tool-calling (`app/ai/agent.py`, up to 8 rounds by default)
-   Database tools are organized in `app/ai/db_tools/` (profile, restaurants, staff, invites, products, suppliers, inventory, file_processing)
-   Access control uses two-layer system:
    - Direct checks (`is_restaurant_owner()`, `has_restaurant_access()`) for simple operations
    - Policy checks (`check_policy_permission()`) for complex tables with centralized management
-   The bot can handle diverse requests autonomously, using tools to interact with the database and file-processing pipelines
-   If a user lacks permission for an operation, the agent returns an error explaining the restriction

Reference: 
- `docs/capabilities.md` (deprecated)
- `docs/resto-pilot-codebase-summary.md` (current architecture)
- `docs/db-tools-patterns.md` (tool architecture, permission patterns, adding new tools)

## Journey format

Each journey below includes:

-   **Goal**: the user outcome
-   **Actor**: who initiates (owner vs staff)
-   **Trigger**: what the user says/does to start the journey
-   **Happy path**: the ideal steps
-   **Minimum required inputs**: what must be collected to succeed
-   **Success criteria**: how we know the journey is done
-   **Guardrails**: “employee mode” behavior constraints
-   **Access control requirements**: what role/scope permissions are needed for the user to complete this journey

---

## J0: User setup (first contact)

-   Goal: register the user and (optionally) connect them to one or more restaurants via an invite code.
-   Actor: owner/operator or outlet staff
-   Trigger:
    -   `/start` (first contact)
    -   `/start <CODE>` (deep-link invite / join flow)

Happy path

1. User sends `/start`; bot creates/updates the user record using Telegram account info.
2. If no invite code is provided:
    - Bot confirms registration.
    - Bot instructs the user to use an invite link/code: `/start <CODE>`.
3. If an invite code is provided:
    - Bot validates the code and, if valid, adds the user to that restaurant (or updates membership if already present).
    - Bot confirms which restaurant was joined and the role assigned (owner vs staff).
4. If profile details are missing (e.g., `full_name`, `phone`):
    - Bot asks for the missing details, one at a time, and stores them.

Minimum required inputs (v1)

-   Telegram message (must come from a Telegram account)
-   Optional: invite code

Success criteria

-   A `users` row exists for the Telegram account (`telegram_id`, `chat_id`, plus any collected details).
-   If invite code provided and valid, a `restaurant_users` membership exists for that `(restaurant_id, user_id)`.
-   The same user can join multiple restaurants over time (one membership row per restaurant).

Guardrails

-   Don’t claim to “verify” a phone number unless verification exists; treat `is_phone_verified` as false unless proven.
-   If the invite code is invalid/expired/used, respond deterministically with a single error message.

Access control requirements

-   This is a reserved Telegram command flow (bypasses normal session batching) and is handled deterministically outside the tool loop.
-   Related concepts in DB: `users`, `restaurants`, `restaurant_users`, `invite_codes` (in v1, “outlet” maps to a `restaurants` row).

## J1: Restaurant setup & onboarding

-   Goal: capture the restaurant’s core operating details so future answers and workflows are grounded in facts.
-   Actor: owner/operator
-   Trigger: owner starts setup (e.g., “set up my restaurant”, “onboard me”, “let’s configure the outlet”).

Happy path

1. Bot explains it will collect a few basics and asks the first question.
2. Owner provides details; bot confirms back succinctly.
3. Bot identifies missing fields and continues until complete.
4. Bot presents a final “setup summary” and asks for confirmation (“Reply YES to confirm”).

Minimum required inputs (v1)

-   Restaurant name
-   Address / location (or area + city)
-   Timezone
-   Opening hours (per day)
-   Primary contact number (for staff-facing responses)

Success criteria

-   A stable “outlet profile” exists (storage mechanism TBD) and can be referenced reliably in later sessions.
-   Bot can answer basic “what are our hours / where are we / contact info” questions using only that profile.

Guardrails

-   Ask **one question at a time**; avoid offering menus/options.
-   If the owner asks for unrelated tasks, redirect to supported operations and ask for missing info.
-   Never claim “saved/updated in the system” unless persistence is implemented.

Access control requirements

-   Owner-only; restaurant-owner scope.

---

## J2: Menu import & publish

-   Goal: get a menu into a structured format the assistant can use for Q&A and operational flows.
-   Actor: owner/operator
-   Trigger: owner provides a menu file/link (e.g., “import our menu”, sends PDF/photo, or a URL).

Happy path

1. Bot asks for the menu source (file upload, photo(s), or URL) if not provided.
2. Bot extracts items, categories, prices, and modifiers (as available).
3. Bot asks targeted clarifying questions for ambiguities (e.g., missing prices, unclear item names).
4. Bot outputs a compact menu summary and asks for confirmation to “publish”.

Minimum required inputs (v1)

-   Menu source (file/photo/URL)
-   Confirmation step (explicit approval to publish)

Success criteria

-   A structured menu exists (storage mechanism TBD) and can be used as the sole source of truth for menu Q&A.
-   Bot can answer “Do we have X?”, “How much is Y?”, “What’s in Z?” from that structured menu.

Guardrails

-   No claims about POS integration unless it exists.
-   Clarify ambiguities with a single question per turn (avoid long multi-question forms).

Access control requirements

-   Owner-only; restaurant-owner scope.

---

## J3: Answer guest questions (staff-facing helper)

-   Goal: help staff respond accurately to common guest questions using configured facts (profile + menu), without hallucinating.
-   Actor: outlet staff (primary); owner indirectly benefits by having consistent responses.
-   Trigger: staff asks a guest-facing question (e.g., “Do we have gluten-free options?”, “What time do we close?”, “Is X spicy?”).

Happy path

1. Bot answers directly from configured sources (outlet profile + menu).
2. If information is missing, bot asks for the one missing fact needed to answer and proposes adding it to the profile/menu (if supported).
3. Bot optionally produces a “sendable” short response staff can paste to a guest.

Minimum required inputs (v1)

-   The question
-   Access to the relevant configured data (profile/menu) OR a way to collect the missing fact from staff

Success criteria

-   Response is correct, short, and doesn’t claim hidden knowledge.
-   When unknown, bot is transparent and asks for the missing fact rather than guessing.

Guardrails

-   Never fabricate allergens, ingredients, availability, or policies.
-   If data is missing, say so and ask one clarifying question; do not provide speculative options.

Access control requirements

-   Restaurant member read access.

---

## J4: Reservation assist (simple)

-   Goal: reduce back-and-forth by collecting reservation details and producing a confirmed request (manual or integrated).
-   Actor: owner or outlet staff
-   Trigger: “Book a table”, “reservation for 4 tomorrow 8pm”, etc.

Happy path

1. Bot collects required fields (date, time, party size, name, contact).
2. Bot confirms the reservation details back as a single summary.
3. Bot finalizes:
    - Manual mode: outputs a ready-to-send message/template for the staff channel.
    - Integrated mode (future): creates the reservation in the system and returns a confirmation.

Minimum required inputs (v1)

-   Date
-   Time
-   Party size
-   Name
-   Contact number

Success criteria

-   Reservation request is complete and unambiguous.
-   If manual mode, staff can copy/paste without edits.
-   If integrated (future), a reservation record exists and a confirmation is sent.

Guardrails

-   Do not claim a booking is confirmed unless the integration exists.
-   Ask one missing-field question per turn; avoid open-ended “anything else?” loops.

Access control requirements

-   Restaurant member access (staff can intake; owners can update policy as needed).

---

## J5: Daily ops brief (owner view)

-   Goal: give the owner a quick, actionable snapshot of today and what to focus on.
-   Actor: owner/operator
-   Trigger: “daily brief”, “what should I focus on today?”, “summary for today”.

Happy path

1. Bot determines what it can base the brief on:
    - If integrations/data exist: use them.
    - If not: ask the owner for the minimal inputs to generate a useful brief.
2. Bot returns a short brief: priorities, risks, and a tight checklist.
3. Bot asks one follow-up question to refine the most important unknown.

Minimum required inputs (v1, no integrations)

-   Today’s hours/plan (if not already in profile)
-   Known constraints the owner provides (e.g., staffing gaps, 86’d items, expected rush windows)

Success criteria

-   Brief is short, specific, and based on provided/configured facts (no invented metrics).
-   Owner gets at least 1–3 concrete actions.

Guardrails

-   Never claim knowledge of reservations/sales/staffing unless those data sources exist.
-   Keep it minimal: avoid long generic management advice.

Access control requirements

-   Owner-only; restaurant-owner scope.
