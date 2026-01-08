# Capabilities and tools (current)

The bot uses a general-purpose agent loop (`app/ai/agent.py`) with tool-calling. The
LLM acts as a function caller: it should attempt tools (or ask for missing info)
before refusing, and refusals should come only from tool/policy checks.

## Memory and refusal behavior

- Conversation memory/history is **context only** (useful for disambiguation).
- Never refuse a request based on memory; always attempt a relevant tool call first.
- If the tool surface changes, the current tool list is authoritative.

## Tool surface (intent-based)

Tools are organized by domain in `app/ai/db_tools/`:

- `profile.py`: user profile read/update
- `restaurants.py`: create/list/get/update restaurants
- `staff.py`: list staff, revoke access (owner only)
- `invites.py`: create/list/delete invite codes (owner only)
- `products.py`: product catalog (owner write, member read)
- `suppliers.py`: supplier directory (owner write, member read)
- `inventory.py`: inventory batches and movements
- `product_aliases.py`: alias management and match confirmation
- `file_processing.py`: invoice/price list/inventory photo processing with review/confirm

## Policies and access control

Access control is enforced in tools via:

- Direct checks for simple rules: `is_restaurant_owner()`, `has_restaurant_access()`
- Allowlist checks for centralized policy control: `check_policy_permission()`

The allowlist lives in `app/policies/db_allowlist.py` and is scoped by:

- `self`
- `owned_restaurant`
- `restaurant_owner`
- `restaurant_member`

Roles are per-restaurant: the same user can be an owner of one restaurant and staff
in another. Tools like `list_my_restaurants` return the role per restaurant.

## File processing pipeline

File ingestion flows use a staging + review model:

1. A tool queues processing (`process_invoice_file`, `process_price_list_file`,
   `process_inventory_photo`).
2. The worker extracts data with a vision model and writes
   `file_processing_staging`.
3. The user reviews (`review_file_processing`) and updates fields if needed.
4. Owners confirm (`confirm_file_processing`) to write into final tables.

Vision model config lives in `.env.example` (`APP_VISION_*`).

## Legacy/optional components

The DBAction engine remains for legacy or deterministic flows (pending actions),
but the primary product path uses intent tools + policy enforcement.
