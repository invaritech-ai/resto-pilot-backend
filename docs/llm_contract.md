# LLM Contract Specification (Phase 1)

## Role
The LLM acts as a **structured parser plugin**. It translates messy user text into precise operations against the database staging area or requests clarification.

---

## Input Schema (LLM Context)
The worker provides the following JSON to the LLM:

```json
{
  "user_message": "string",
  "recent_history": [
    {"role": "user|bot", "content": "string"}
  ],
  "restaurant_context": {
    "name": "string",
    "suppliers": ["name1", "name2"]
  },
  "pending_state": {
    "uploads": [
      {"id": "uuid", "supplier": "string", "item_count": 5, "last_items": ["..."]}
    ]
  },
  "allowed_operations": ["patch_staging", "ask_clarification", "execute_command"]
}
```

---

## Output Schema (Strict JSON)
The LLM MUST return a single JSON object.

### 1. Patch Operation
Used when intent is clearly a modification to a pending record.
```json
{
  "action": "patch_staging",
  "staging_id": "uuid",
  "patches": [
    { "op": "replace", "path": "/items/0/price_minor", "value": 500 }
  ],
  "reasoning": "User asked to change tomato price to $5"
}
```

### 2. Clarification
Used when intent is ambiguous or missing required data.
```json
{
  "action": "ask_clarification",
  "question": "Which supplier's price list are you referring to? ABC or XYZ?",
  "options": ["ABC", "XYZ"]
}
```

---

## Safety & Validation
All LLM outputs are piped through a **Validator Worker** before application:
1. **Schema Check**: Validates against the JSONPatch RFC 6902 structure.
2. **Whitelist Paths**: Only specific paths in the staging JSON are patchable:
   - `/items/*/price_minor`
   - `/items/*/name`
   - `/items/*/unit`
3. **Value Sanitization**: Prices must be integers; currencies must be valid ISO codes.
4. **Staging Lock**: Patches only apply to `file_processing_staging.extracted_data_json`. Direct writes to final production tables are FORBIDDEN via LLM.

---

## Intent Mapping
- **"Tomato to 5"** -> `patch_staging`
- **"What did I upload?"** -> `execute_command` (`/uploads`)
- **"Add ABC"** -> `execute_command` (`/add supplier ABC`)
- **"Huh?"** -> `ask_clarification`
