# Button Schema Specification (Phase 1)

## Wire Format: `action:id:param`
Telegram `callback_query.data` has a **64-byte limit**. We use use a compact, colon-delimited string format.

- `action`: Fixed mnemonic (3-6 chars)
- `id`: UUID (hex without dashes to save space) or short index
- `param`: Optional modifier

---

## Action Registry

| Action Key | Payload Example | Meaning |
| :--- | :--- | :--- |
| `rev_u` | `rev_u:uuid` | Review Upload (displays staging items) |
| `conf_u` | `conf_u:uuid` | Confirm Upload (final DB write) |
| `del_u` | `del_u:uuid` | Delete Pending Upload |
| `ed_row` | `ed_row:uuid:idx`| Edit Row (triggers edit flow for specific index) |
| `list_p` | `list_p:page:2` | List Pagination (target page) |
| `res_h` | `res_h:uuid:yes` | Resolve Handshake (e.g., confirm unit conversion) |

---

## Common Layouts

### 1. Pending Upload List
Shown when user runs `/uploads` or starts a new upload with pending items.
```text
[ 📄 Review: ABC Supplier ] -> callback: `rev_u:uuid_abc`
[ 📄 Review: XYZ Supplier ] -> callback: `rev_u:uuid_xyz`
```

### 2. Upload Review Summary
Shown after OCR or manually via "Review".
```text
[ ✅ Confirm All ] -> callback: `conf_u:uuid`
[ ❌ Delete This ] -> callback: `del_u:uuid`
[ ✏️ Edit Item #1 ] -> callback: `ed_row:uuid:1`
[ ✏️ Edit Item #2 ] -> callback: `ed_row:uuid:2`
```

### 3. Handshake Question
```text
Question: Is "case" for Tomatoes equal to 10kg?
[ ✅ Yes ] -> callback: `res_h:uuid:yes`
[ ❌ No  ] -> callback: `res_h:uuid:no`
```

---

## Implementation Rules
1. **Deterministic Execution**: Button handlers are strictly deterministic. They MUST NOT call LLMs.
2. **Expired ID Handling**: If a button references an ID already deleted, the handler responds with an alert: "This item no longer exists."
3. **State Updates**: After a button action (e.g., `conf_u`), the bot should edit the original message to reflect the new state (e.g., "✅ Upload Confirmed") to prevent double-clicks.
