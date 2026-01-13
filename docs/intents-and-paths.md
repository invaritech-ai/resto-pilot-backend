# Intents and user journeys (static bot)

This doc enumerates supported intents and the manual test paths.
It reflects current behavior in `app/ai/intent_classifier.py` and `app/conversation/*`.

## Global behavior

- Onboarding: `/start` registers a user; if phone is missing, the bot prompts for it.
- Invites: `/start CODE` accepts invite codes and joins a restaurant.
- Navigation: `/menu` or `/help` shows the menu. `/cancel` cancels an active operation.
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

### Inventory
- `list_inventory` (`restaurant_id`)
- `add_inventory` (`restaurant_id`, `product_name`, `quantity`, `unit`) - currently returns a prompt to upload an invoice
- `update_inventory` (`batch_id`, `quantity`) - currently returns a placeholder response

### File processing
- `upload_price_list` (`file_id`, `restaurant_id`)
- `upload_invoice` (`file_id`, `restaurant_id`)
- `confirm_upload` (`staging_id`)

### Navigation
- `show_menu`
- `cancel`
- `help` (`topic`, optional)
- `unknown`

Note: outlet/supplier/staff selection does not currently resolve names or menu numbers to IDs.
Manual tests should pass explicit IDs or implement selection mapping.

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
1) User: "update my name"
2) Bot asks for name.
3) User provides name.
4) Bot confirms update.

### Profile: update phone
1) User: "update phone"
2) Bot asks for phone.
3) User provides phone.
4) Bot confirms update.

### Outlets: list
1) User: "list outlets"
2) Bot lists outlets with roles.

### Outlets: add
1) User: "add outlet"
2) Bot asks for name.
3) User provides name.
4) Bot confirms creation.

### Outlets: update
1) User: "update outlet"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id` (or implement selection mapping).
4) Bot asks for new name.
5) Bot confirms update.

### Staff: list
1) User: "list staff"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Bot lists staff.

### Staff: add (invite)
1) User: "add staff"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Optional: provide role (`staff` or `owner`); defaults to `staff`.
5) Bot returns invite link.

### Staff: revoke
1) User: "revoke staff"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Bot asks for `user_id`.
5) Provide `user_id`.
6) Bot confirms removal.

### Suppliers: list
1) User: "list suppliers"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Bot lists suppliers.

### Suppliers: add
1) User: "add supplier"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Bot asks for supplier name.
5) Bot confirms creation.

### Suppliers: update
1) User: "update supplier"
2) Bot asks for `supplier_id`.
3) Provide `supplier_id` and new name.
4) Bot confirms update.

### Suppliers: view
1) User: "view supplier"
2) Bot asks for `supplier_id`.
3) Provide `supplier_id`.
4) Bot shows details.

### Inventory: list
1) User: "list inventory"
2) If multiple outlets, bot asks which outlet.
3) Provide `restaurant_id`.
4) Bot lists recent batches.

### Inventory: add (current behavior)
1) User: "add inventory"
2) Bot replies with instructions to upload an invoice.

### Inventory: update (current behavior)
1) User: "update inventory"
2) Bot replies with a placeholder response.

### File upload
1) User uploads a file with a caption containing "invoice" or "price list".
2) If multiple outlets and no active outlet, bot asks which outlet.
3) System enqueues file processing tasks.
4) User uses `/confirm` after review.

### Fallback
Any other message returns "I can't help with that."
