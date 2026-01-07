# Unified flow refactor checklist (batching enabled)

**Note**: This checklist references some outdated components. The capability gating system has been replaced with a general-purpose agent with tool-calling. See `docs/capabilities.md` for migration notes.

Use this as a step-by-step checklist to align the code with `docs/unified-message-flow.md`, updating **one function at a time**.

## 0) Decisions (do first)

- [x] Choose the single routing source of truth for batching-enabled mode (webhook vs `handle_update`) and commit to it.
- [x] Confirm ack policy:
  - [x] Per-message receipt ack uses cheap LLM with ~40% drop rate, sent immediately after ingest.
  - [x] Flush ack is static “I’m on it” or skipped (no cheap LLM on flush).
- [x] Define the initial “instant stateful” states (e.g., `COLLECT_PHONE`, `PENDING_CONFIRM_WRITE`) and where they are persisted.

## 1) Webhook routing (batching enabled)

File: `app/api/v1/routes/telegram.py`

- [ ] Implement/verify the precedence rules:
  - [x] `/respond` and `/done` force-flush immediately.
  - [x] `/start` (including `/start <code>`) routes to instant handling.
  - [ ] Other standalone `/...` commands (future) can be declared instant.
  - [x] Instant stateful routes are checked before default ingest.
- [x] Add instant stateful detection:
  - [x] Parse `telegram_id` and load the `users` row (by `telegram_id`).
  - [x] If `users.state` indicates an instant flow, enqueue `handle_telegram_update(update)` and return `200`.
- [x] Ensure the default non-instant path remains `ingest_update(update)` and returns `200`.

## 2) Ingest (per-message ack + batching)

File: `app/telegram/ingest.py`

- [ ] Confirm ingest behavior:
  - [x] Requires registered user exists; otherwise deterministic behavior is chosen (drop vs route-to-start UX).
  - [x] Creates/reuses an open `telegram_sessions` row and inserts `telegram_messages`.
  - [x] Schedules `flush_session` debounce.
- [x] Confirm per-message ack behavior:
  - [x] Cheap LLM backchannel is attempted after successful message ingest.
  - [x] Drop rate ~40% (send ~60%).
  - [x] Ensure this is the primary “instant feedback” mechanism for normal messages (not flush-time).

## 3) Flush (debounce seal + optional static ack)

File: `app/workers/tasks.py` (`flush_session`)

- [ ] Verify flush logic:
  - [x] Only seals if session is open and expected timestamp matches (stale flush no-ops).
  - [x] Transitions `open -> processing` and enqueues `process_session` immediately.
- [ ] Adjust flush ack behavior to match policy:
  - [x] Ensure any flush-level ack is static or skipped (no cheap LLM here).
  - [x] Avoid duplicate “receipt” acks at flush time.

## 4) Instant handler (commands + instant stateful)

Files:
- `app/workers/tasks.py` (`handle_telegram_update`)
- `app/telegram/handler.py` (`handle_update`)
- `app/telegram/processor.py` (`process_update`)

- [ ] `/start` flow:
  - [x] Registers/updates the user deterministically.
  - [x] Accepts `/start <code>` invites (membership upsert) deterministically.
  - [x] If phone missing: sets `users.state="COLLECT_PHONE"` and prompts immediately.
- [ ] Instant stateful (phone intake):
  - [x] When `users.state=="COLLECT_PHONE"`, parse phone deterministically and store it (no verification).
  - [x] Reset `users.state="IDLE"` after storing phone.
  - [x] Immediate user-facing response (no batching delay).
- [ ] `/respond` + `/done` force flush behavior:
  - [ ] Ensure there is only one “canonical” force-flush implementation path in batching-enabled mode (webhook vs handler), and remove/avoid drift.
  - [x] Add `/confirm` and `/cancel` as instant commands for DB pending action resolution.

## 5) Session processing pipeline (session-level only)

Files:
- `app/processing/session_processor.py`
- `app/ai/topic_gate.py`
- `app/ai/agent.py` (general-purpose agent with tool-calling)
- `app/ai/db_tools.py` (database tools)

- [x] Keep `process_session` focused on session-level processing:
  - [x] Topic gate (cheap model) → redirect/ghost if off-topic.
  - [x] Run general-purpose agent loop with tool-calling support.
  - [x] Persist outgoing message and update chat memory summary.
  - [x] Record each LLM call individually in `llm_calls` for cost tracking.
- [ ] Name hint support (optional):
  - [ ] Confirm the prompt only uses first name sparingly and doesn’t force name usage in every reply.

## 6) Tests (add as you refactor)

- [ ] Webhook routing tests (batching enabled):
  - [x] Normal message routes to `ingest_update`.
  - [x] `/start` routes to `handle_telegram_update`.
  - [x] `/respond` `/done` force-flush and enqueue `process_session`.
  - [x] Instant stateful (e.g., `COLLECT_PHONE`) routes to `handle_telegram_update`.
- [ ] Phone intake end-to-end (batching enabled):
  - [x] `/start` sets `COLLECT_PHONE`.
  - [x] Next message with phone is handled immediately (no debounce wait) and persists `users.phone`.

## 7) Follow-on (after flow is stable)

- [x] General-purpose agent with tool-calling implemented (`app/ai/agent.py`).
- [x] Database tools with role/scope-based access control (`app/ai/db_tools.py`).
- [x] All LLM calls recorded individually for cost tracking.
- [ ] Add more database tools as needed (e.g., supplier management, inventory operations).
- [ ] Add deterministic confirmation + deterministic DB write path for CUD operations (if needed).
