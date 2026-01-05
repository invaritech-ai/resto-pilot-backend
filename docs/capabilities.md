# Capability lock-down (employee mode)

This project intentionally runs the “main processing bot” as an employee with **very limited, explicitly enabled capabilities**. The goal is to prevent the assistant from:
- offering options it cannot execute,
- claiming access to internal data (outlets, accounts, inventory records),
- drifting into “helpful” plans/checklists when the product can’t actually do them yet.

The design is **default deny**:
1) A capability must be explicitly enabled.
2) The request must match that capability.
3) The model output is post-checked and replaced if it violates employee rules.

## What is a “capability”?

A capability is a single, narrow “job the bot is allowed to do” (e.g., *take inventory update requests*).

Capabilities are not (yet) “tools” or “actions executed against a backend”. They are a **behavior contract**:
- The bot only collects the minimum required info for that job.
- It does not promise to “fetch” or “list” things that do not exist in the backend.
- If the user asks for anything else, the bot rejects once, then ghosts until the conversation returns to the enabled capability.

## Where capability enforcement happens

There are three enforcement layers. You generally want all three.

### 1) Pre-LLM capability gate (hard reject)

This is the most important layer: do not rely on the model to “behave”.

- Enabled capability list: `APP_ENABLED_CAPABILITIES_CSV` (comma-separated)
  - Config: `app/core/config.py` (`Settings.enabled_capabilities_csv`)
  - Example: `.env.example` (`APP_ENABLED_CAPABILITIES_CSV=inventory`)
- Capability classifier:
  - `app/ai/capability_gate.py` (`classify_capability`)
  - Returns `(supported: bool, reject_text: str)`

Enforced in session processing:
- `app/processing/session_processor.py`:
  - If unsupported: send one `capability_reject` message, set `telegram_chat_states.off_topic_mode=true`, close session.
  - If already in ghosting mode: do nothing (“ghost”).

Note: `telegram_chat_states.off_topic_mode` currently acts as a general “ghosting mode” for *both* topic-gating and capability-gating.

### 2) System prompt restriction (reduce hallucinations)

Prompts are not sufficient alone, but they reduce the frequency of violations.

Main processing prompts (employee rules):
- `app/ai/session_reply.py`:
  - `generate_session_reply()` system prompt
  - `generate_session_reply_with_metrics()` system prompt

Other prompts (not the main bot, but still relevant):
- Topic gate classifier prompt: `app/ai/topic_gate.py`
- Chat memory summarizer prompt: `app/ai/chat_memory.py`
- Cheap backchannel (ack) prompt: `app/ai/backchannel.py`

### 3) Runtime output guard (hard filter)

Even with a strict system prompt, LLMs can still output:
- “I can list your outlets”
- “Here are your options”
- “What account email should I use”

So after the model responds we validate its text and replace it with a safe fallback if needed.

- Guard rules live in: `app/ai/reply_guard.py`
- Applied in: `app/ai/session_reply.py` (both reply functions)

This is the “seatbelt”: it’s a last line of defense.

## How to add a new capability (one at a time)

Capabilities are intentionally added incrementally, with a small surface area.

### Step 0: choose the capability slug + allowed behavior

Pick a slug like:
- `inventory`
- `invoices`
- `pricing`

Write down:
- what the bot is allowed to do (1–2 sentences),
- what information it is allowed to ask for (fields),
- what it must never claim (data access, actions).

### Step 1: enable it in configuration

Set `.env`:
- `APP_ENABLED_CAPABILITIES_CSV=<slug>`

If you want multiple capabilities later (not recommended until v1 is stable):
- `APP_ENABLED_CAPABILITIES_CSV=inventory,pricing`

### Step 2: teach the pre-LLM capability gate how to recognize it

Edit `app/ai/capability_gate.py`:

1) Add the slug check in `get_enabled_capabilities()`/`classify_capability()`.
2) Add “positive match” signals, usually:
   - a hint command (e.g. `/pricing`)
   - keyword(s) in text/caption (cheap string match)
3) Choose a single reject message that:
   - does not offer options,
   - asks one question,
   - pulls the user back to the enabled capability.

Example pattern inside `classify_capability()`:
- If enabled contains `<slug>` and message matches `<slug>` keywords → supported.
- Else → `(False, "<reject + one question>")`

### Step 3: update the main bot system prompt to match the capability

Edit `app/ai/session_reply.py` system prompts:
- The prompt should literally state the single capability.
- Explicitly deny access to data you don’t have.
- Forbid offering menus/options.

Important: prompts must not describe capabilities that aren’t truly implemented.

### Step 4: add special-case refusals (optional, but recommended)

If there are known “trap requests” that trigger hallucinations (like “list my outlets”), detect them before the model and respond deterministically.

Current example:
- Outlet list request detection: `app/ai/capability_gate.py` (`is_outlet_list_request`)
- Deterministic refusal: `inventory_outlet_list_refusal()`
- Enforced early in `app/processing/session_processor.py` (skips calling the expensive model).

### Step 5: tune the runtime output guard

If the model still slips, expand `app/ai/reply_guard.py`:
- add more disallowed phrases/patterns,
- tighten length/question limits,
- add more “never ask for X” tokens.

The fallback should be the “safe employee question” (single question).

### Step 6: update docs + run manual tests

Update:
- `.env.example` (if you add a new env/config field)
- `docs/bot-conversation-paths.md` (if the behavior changes)

Manual test checklist:
- Send a clearly unsupported request → receive **one** capability-reject message; next unsupported request is ghosted.
- Send a supported request → bot asks only for missing fields (no options, no “I can…”).
- Send “list my outlets” → deterministic refusal (no model call).

## How to redact (remove/disable) a capability

“Redacting” a capability means the bot must stop recognizing/responding as if it can do that job.

1) Remove the slug from `.env`:
   - `APP_ENABLED_CAPABILITIES_CSV=...` (omit the slug)
2) Remove or disable its “positive match” logic in `app/ai/capability_gate.py`.
3) Update the main system prompts in `app/ai/session_reply.py` so they no longer mention that capability.
4) Remove any special-case detectors/refusals that only exist for that capability (optional).

If a capability is removed but remains described in the prompt, you will get hallucinated behavior again.

## Where the “anti-hallucination” prompts live

Main bot prompts:
- `app/ai/session_reply.py` (two `system_prompt = (...)` blocks)

Supporting prompts:
- `app/ai/topic_gate.py` (cheap on-topic classifier)
- `app/ai/chat_memory.py` (memory summarizer)
- `app/ai/backchannel.py` (cheap ack generator)

Hard validation (not a prompt):
- `app/ai/reply_guard.py`

