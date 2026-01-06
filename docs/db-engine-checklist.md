# DB engine checklist

- [x] Define the DB action payload schema (fields, enums, required vs optional).
- [x] Implement payload parsing + validation (schema-level).
- [x] Implement policy validator against `app/policies/db_allowlist.py` (role/table/CRUD/columns/scope).
- [ ] Add deterministic normalization (enforce scope filters, strip disallowed fields).
- [ ] Add confirmation state model (pending action stored + confirm/cancel flow).
- [ ] Implement executor for `read` (no confirmation) with strict allowlist checks.
- [ ] Implement executor for `create|update|delete` (requires confirmation).
- [ ] Add audit logging for every decision (allowed/denied + reason).
- [ ] Add tests for each role/table/CRUD/scope edge case.
