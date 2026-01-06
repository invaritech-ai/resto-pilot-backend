# DB engine checklist

- [x] Define the DB action payload schema (fields, enums, required vs optional).
- [x] Implement payload parsing + validation (schema-level).
- [x] Implement policy validator against `app/policies/db_allowlist.py` (role/table/CRUD/columns/scope).
- [x] Add deterministic normalization (enforce scope filters, strip disallowed fields).
- [x] Add confirmation state model (db_pending_actions).
- [x] Implement confirm/cancel flow for pending actions.
- [x] Implement executor for `read` (no confirmation) with strict allowlist checks.
- [x] Implement executor for `create|update|delete` (requires confirmation).
- [x] Add audit logging for every decision (allowed/denied + reason).
- [x] Add tests for each role/table/CRUD/scope edge case.
