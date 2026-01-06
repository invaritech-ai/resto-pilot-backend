# Unified message flow (batching enabled)

This doc defines the **target unified message flow** when `telegram_batching_enabled=true`.

Goals:
- Immediate user feedback (best-effort) without waiting for the batching debounce window.
- A single, consistent routing model that supports:
  - **instant commands** (`/start`, `/respond`, `/done`, and future standalone `/...` commands),
  - **instant stateful routes** (multi-step flows like phone intake or DB-write confirmations),
  - **batched sessions** (normal conversational messages).
- Deterministic database writes (especially for confirmations and CRUD), with LLM used for:
  - cheap per-message backchannel ack (optional),
  - routing/classification (cheap model),
  - phrasing user-facing confirmation messages (but not executing writes).

---

## Terminology

- **Webhook**: FastAPI endpoint receiving Telegram updates.
- **Ingest**: persist message to DB, assign it to an open session, and schedule flush.
- **Flush**: seal an open session for processing after debounce or by command.
- **Instant route**: bypass batching, handled immediately by a worker task.
- **Instant stateful route**: an instant route that depends on persisted user/chat state (e.g., `COLLECT_PHONE`, pending confirmations).
- **Per-message ack**: best-effort, cheap LLM message acknowledging receipt with ~40% drop rate.
- **Flush ack**: optional, static “I’m on it” style message when a session transitions to processing; not the primary feedback channel.

---

## Proposed unified flow (high level)

### A) Webhook ingress (single routing decision point)

For each update:
1) Parse update: `chat_id`, `telegram_id`, `text/caption`, `command`.
2) Route to **instant** if any of the following is true:
   - command is in `{/respond, /done}` (force flush),
   - command is `/start` (including `/start <code>` deep-link),
   - command is any other standalone `/...` command we choose to treat as instant,
   - user/chat is in an **instant stateful** mode (e.g., phone intake, pending DB-write confirmation).
3) If instant: enqueue `handle_telegram_update(update)` and return `200`.
4) Else (default): call `ingest_update(update)` and return `200`.

This ensures normal messages use batching, while “must respond now” flows do not get stuck behind debounce.

### B) Instant handler (deterministic + immediate response)

Worker task: `handle_telegram_update(update)` delegates to `handle_update(update, db, settings)`.

Responsibilities:
- `/start` and `/start <code>`:
  - create/update the user,
  - accept invite codes (membership upsert),
  - prompt for phone collection if missing (or other onboarding).
- `/respond` and `/done`:
  - deterministically seal the current open session (if any),
  - enqueue `process_session(session_id)` immediately.
- Instant stateful routes:
  - deterministically parse user response (YES/NO, phone number, etc.),
  - apply deterministic DB writes if confirmed/valid,
  - advance/reset state.

### C) Batched ingestion (append-only + per-message ack)

Default path: `ingest_update(update)`:
- Lock chat
- Ensure user exists (if not registered, drop or route to `/start` UX depending on product choice)
- Create/reuse an **open** `telegram_sessions` row
- Insert `telegram_messages`
- Schedule `flush_session(...)` after debounce
- Send **per-message ack** using a cheap LLM with ~40% drop rate

This is the primary mechanism for “don’t make the user wait 30s for feedback”.

### D) Flush (debounce seal)

Worker task: `flush_session(session_id, expected_last_activity_at)`:
- If session still open and expected timestamp matches:
  - Seal: `open -> processing`
  - Enqueue `process_session(session_id)` immediately
  - Optional: send **flush ack** (static “I’m on it”) or skip
- Else: no-op

Important: flush ack is not required and should not replace per-message feedback.

### E) Session processing pipeline (where routing LLM lives)

Worker task: `process_session(session_id)` runs the main pipeline:
1) Load session messages + attachments + history context.
2) Convert attachments to text (future work):
   - files -> text (OCR/parse)
   - audio -> text (ASR)
3) Cheap routing LLM (gate model) returns strict JSON:
   - on-topic/off-topic,
   - whether the user is attempting **CRUD against DB tables**,
   - which allowlisted table/operation/fields apply.
4) Enforce:
   - off-topic redirect + ghosting (if applicable),
   - capability gating (must include user updates / DB CRUD paths as enabled capabilities).
5) Execute deterministically:
   - **Read**: query DB and present results (LLM can phrase the final response using deterministic results).
   - **Write**: propose change + ask for confirmation; only apply write on explicit confirmation in an **instant stateful** path.

---

## Ack policy (required)

### Per-message ack (cheap LLM)
- Trigger: after each successfully ingested message (batched path)
- Rate: ~60% send, ~40% skip
- Timing: immediate (no need to wait for session flush)
- Purpose: quick “receipt” feedback; not a full response

### Flush ack (static or skip)
- Trigger: when session transitions to `processing`
- Content: static “Got it — I’m on it.” or no message
- Purpose: optional; should not be relied on for responsiveness

---

## Decision table (batching enabled)

Legend:
- Route = `instant` means enqueue `handle_telegram_update(update)` (immediate worker handling)
- Route = `batched` means `ingest_update(update)` (create/reuse open session + schedule flush)

| Condition | Route | DB actions (high level) | Ack behavior |
|---|---|---|---|
| `command in {"/respond","/done"}` | instant | append to current open session (if any) + seal open→processing + enqueue `process_session` | optional static flush-ack OR skip |
| `command == "/start"` (includes `/start <code>`) | instant | get/create user; accept invite code (upsert membership); may set user state | respond immediately (no per-message cheap ack) |
| `command is other standalone "/X"` (future) | instant | deterministic command handler; may write DB | respond immediately (no per-message cheap ack) |
| `user_state in {COLLECT_PHONE, PENDING_CONFIRM_WRITE, ...}` (instant stateful) | instant | deterministic parse/update state and/or apply confirmed write; persist audit | respond immediately (deterministic or LLM-authored prompt as needed) |
| otherwise (normal message; no command; no stateful pending) | batched | ingest into open session; persist message; schedule flush | per-message cheap ack ~60% right after ingest |

### Precedence (routing order)
1) `/respond` `/done` (force-flush commands)
2) `/start` (and `/start <code>`)
3) other instant slash commands (if defined)
4) instant stateful routes (based on persisted user/chat state)
5) default batched ingest

---

## Notes on `/start <code>` and latency

- The webhook should remain fast because it **enqueues** work to Celery and returns immediately.
- `/start <code>` for existing users should be supported as an idempotent “add me to restaurant” path (membership upsert).
- The worker may do a few DB reads/writes (user upsert, invite validation, membership upsert), but this should not affect webhook latency.

