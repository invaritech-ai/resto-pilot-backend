# Router Specification (Phase 1)

## Principle: Stateless-per-Message
The system operates as a stateless engine where each incoming message is interpreted based on its content and the current state of the database (e.g., pending uploads), rather than an ephemeral "active flow" that blocks other commands.

## Message Routing Precedence
Every message is evaluated in order. The first match wins.

| Priority | Matcher | Handler | Purpose |
| :--- | :--- | :--- | :--- |
| 1 | **Global Reset** | `reset_handler` | `home`, `cancel`, `start` - Clears transient UX state. |
| 2 | **Button Callback** | `button_router` | Handles Telegram `callback_query` from inline buttons. |
| 3 | **Slash Command** | `command_router` | `/list`, `/add`, `/uploads` - Standard functional entry points. |
| 4 | **File Upload** | `file_router` | Handles `document` or `photo` objects. Checks DB for pending items. |
| 5 | **Structured Pattern** | `regex_router` | Matches "5kg tomato" or "#2" - High-speed deterministic parsing. |
| 6 | **Free-form Text** | `llm_router` | Fallback to LLM for intent extraction and patching. |

---

## Handler Details

### 1. Global Reset
Matches literal text (case-insensitive): `home`, `menu`, `cancel`, `exit`, `/start`.
- **Action**: Clears `telegram_sessions.context_json`.
- **Response**: Main Menu or standard greeting.

### 2. Button Router
Parses `callback_query.data`. 
- Data format: `action:id:param` (e.g., `confirm_upld:uuid-123`).
- **Constraint**: Buttons must always be valid unless the resource they point to (e.g., staging record) is deleted.

### 3. File Router
When a file is received:
1. Query `file_processing_staging` for `status='pending_review'`.
2. If records exist: Prompt user to resolve pending items or start new.
3. If none: Trigger `document_worker` for OCR/Parsing.

### 4. LLM Router (Fallback)
If no deterministic match is found:
1. Pull context: User info, last 10 messages, list of relevant pending DB records.
2. Prompt LLM to classify intent or propose a patch for a pending record.
3. **Safety**: LLM results are validated against schema before any staging write. Never writes directly to final tables.

---

## UX Guardrails
- **No Blocking**: A user mid-upload review can still run `/list suppliers`. The result is shown, and the upload remains "pending" in the DB, accessible via `/uploads` or buttons.
- **Disambiguation**: If an input is ambiguous (e.g., "delete it"), the system responds with numbered options based on the message history.
