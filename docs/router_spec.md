# Telegram Router Spec

The router is deterministic and evaluates each incoming update in strict priority order (first match wins):

1. Global reset words (`home`, `menu`, `cancel`, `exit`, `/start`)
2. Button callbacks (`callback_query`)
3. Slash commands (`/` prefix)
4. File uploads (`document` or `photo`)
5. Structured patterns (`#N`, qty+unit regex)
6. Fallback handler

## Design intent
- Keep high-confidence intents in deterministic handlers.
- Avoid blocking state machines: users can run commands even while uploads are pending.
- Persist critical process state in DB rather than ephemeral in-memory flows.

## Error handling rules
- Handler exceptions bubble up to worker retry path.
- Dedup records are removed on failed worker execution to allow retry.
- Callback handlers return user-facing alerts for stale/invalid payloads where possible.

## Security/tenant intent
- Routing does not grant access by itself.
- Service/handler layers must validate restaurant membership and ownership for mutating operations.
