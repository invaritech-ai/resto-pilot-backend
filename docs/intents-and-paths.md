# Intents and user journeys (static bot)

This doc enumerates supported intents and the manual test paths.
It reflects current behavior in `app/ai/intent_resolver.py` and `app/conversation/*`.

## Architecture

The message processing flow uses 2 LLM calls:

1. **Intent Resolution** (`app/ai/intent_resolver.py`): Single LLM call that classifies intent AND resolves all entity names to UUIDs using provided candidates.

2. **Response Formatting** (`app/conversation/processor.py`): LLM call to format the action result into a user-friendly response.

The executor (`app/conversation/executor.py`) receives fully resolved params and just executes the action.

## Global behavior

- Onboarding: `/start` registers a user; if phone is missing, the bot prompts for it.
- Invites: `/start CODE` accepts invite codes and joins a restaurant.
- Navigation: `/menu` or `/help` shows the menu. `/cancel` cancels.
- Topic help: "what can you do in profile/suppliers/etc?" returns a short help message.
- File confirmation: `/confirm` confirms a pending file upload.
- Fallback: unknown requests return "I can't help with that."

## Intent catalog

### Profile
- `view_profile` (no params)
- `update_name` (`name`)
- `update_phone` (`phone`)

### Outlet (restaurant)
- `list_outlets` (no params)
- `add_outlet` (`name`)
- `update_outlet` (`restaurant_id`, `name`)

### Staff
- `list_staff` (`restaurant_id`)
- `add_staff` (`restaurant_id`, `role`)
- `revoke_staff` (`restaurant_id`, `user_id`)

### Supplier
- `list_suppliers` (`restaurant_id`)
- `add_supplier` (`restaurant_id`, `name`)
- `update_supplier` (`supplier_id`, `name`)
- `view_supplier` (`supplier_id`)
- `view_supplier_price_list` (`supplier_id`)
- `view_supplier_items` (`supplier_id`)
- `deactivate_supplier` (`supplier_id`)

### Invites
- `list_invites` (`restaurant_id`)
- `move_invite` (`restaurant_id`, `invite_code`)

### Inventory
- `list_inventory` (`restaurant_id`)
- `add_inventory` - returns a prompt to upload an invoice
- `update_inventory` - returns a placeholder response
- `log_inventory_usage` (`batch_id`, `quantity`, `reason`)
- `list_locations` (`restaurant_id`)
- `add_location` (`restaurant_id`, `name`)

### Invoices
- `list_invoices` (`restaurant_id`)
- `view_invoice` (`invoice_id`)

### File processing
- `upload_price_list`
- `upload_invoice`
- `confirm_upload` (`staging_id`)

### Navigation
- `show_menu`
- `cancel`
- `help` (`topic`, optional)
- `unknown`

Note: The intent resolver uses fuzzy matching to resolve entity names to UUIDs from the provided candidates.

## User journeys and manual test paths

### Onboarding
1) User sends `/start`.
2) Bot registers user and (if phone missing) prompts for phone.
3) Optional: `/start CODE` accepts invite code and confirms join.

### Menu
- User sends `/menu` or `/help` and receives the main menu text.

### Profile: view
1) User: "show my profile"
2) Bot responds with name, phone, and username.

### Help: topic
1) User: "what can you do in profile?"
2) Bot responds with profile options (view/update name/phone).

### Profile: update name
1) User: "update my name to John"
2) Bot confirms update.

### Profile: update phone
1) User: "update phone to +1 555 123 4567"
2) Bot confirms update.

### Outlets: list
1) User: "list outlets"
2) Bot lists outlets with roles.

### Outlets: add
1) User: "add outlet Main Kitchen"
2) Bot confirms creation.

### Outlets: update
1) User: "rename outlet Main Kitchen to Downtown Kitchen"
2) Bot confirms update.

### Staff: list
1) User: "list staff" or "list staff for Main Kitchen"
2) Bot lists staff.

### Staff: add (invite)
1) User: "invite staff to Main Kitchen"
2) Bot returns invite link.

### Staff: revoke
1) User: "remove John from Main Kitchen"
2) Bot confirms removal.

### Suppliers: list
1) User: "list suppliers"
2) Bot lists suppliers.

### Suppliers: add
1) User: "add supplier Fresh Farms"
2) Bot confirms creation.

### Suppliers: update
1) User: "rename Fresh Farms to Fresh Farms Inc"
2) Bot confirms update.

### Suppliers: view
1) User: "view Fresh Farms"
2) Bot shows details.

### Suppliers: price list
1) User: "show price list for Fresh Farms"
2) Bot shows price list.

### Inventory: list
1) User: "list inventory"
2) Bot lists recent batches.

### File upload
1) User uploads a file with a caption containing "invoice" or "price list".
2) System enqueues file processing tasks.
3) User uses `/confirm` after review.

### Fallback
Any other message returns "I can't help with that."
