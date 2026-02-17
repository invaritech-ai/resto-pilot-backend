# Flow States Specification (Phase 1)

## Persistent State: The Source of Truth
State is primarily stored in the database, ensuring that user progress (e.g., an uploaded price list) is never lost due to session expiry or bot restarts.

### 1. File Processing Staging (`file_processing_staging`)
This table holds the "state" of any upload-in-progress.
- **`status`**: `processing`, `pending_review`, `confirmed`, `cancelled`.
- **`extracted_data_json`**: The current canonical state of the parsed document.
- **Lifecycle**: Content is modified here (via buttons or LLM patches) until the user hits **Confirm**.

### 2. Handshake Questions
For unresolved entities (e.g., unknown supplier or unit), questions are logged/tracked to ensure the bot can resume if the user answers later.
- **Model**: `handshake_requests` (id, user_id, type, context_data, resolved).

---

## Transient Context (`telegram_sessions.context_json`)
Used for lightweight, UX-specific data that is "nice-to-have" but not critical for system integrity.

| Field | Purpose | Example |
| :--- | :--- | :--- |
| `last_list_type` | To interpret "next" or "prev" commands. | `suppliers` |
| `last_list_offset` | Pagination tracking. | `20` |
| `numbered_items` | To resolve "Edit #2". | `["uuid-product-a", "uuid-product-b"]` |
| `active_staging_id` | Quick reference for the current reviewed file. | `uuid-staging` |

---

## State Transition Logic (Non-Blocking)

### Upload Handling
1. **Receipt**: Check for existing `pending_review` rows.
2. **Prompt**: "You have 1 pending upload. Review it or start new?"
3. **New**: Creates new `file_processing_run` + `staging` record.
4. **Review**: Loads `staging.extracted_data_json` into message view.

### Correction Loop
1. User types "change price of tomatoes to 5".
2. System identifies the `active_staging_id` from session (or queries the latest `pending_review` if session is empty).
3. LLM generates JSONPatch.
4. Patch applied to `staging.extracted_data_json`.
5. User must click **Confirm** for final write to `supplier_prices`.

## Recovery
If a user walks away for 24 hours:
- Session context may be cleared.
- Commands like `/uploads` or `/list_pending` query the DB directly to restore the `active_staging_id` and resume the review flow.
