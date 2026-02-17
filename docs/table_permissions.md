| Table | Create Permission | Read Permission | Update Permission | Delete Permission |
| --- | --- | --- | --- | --- |
| `users` | Any user can create their account (via `/start`). | Users can read their own profile only. | Users can update their own profile fields (name, phone, username). | Not allowed (system manages). |
| `restaurants` | Owners can create new restaurants/outlets they own. | Owners and staff see restaurants they belong to. | Only restaurant owners can edit their restaurant details. | Only owners can soft-delete (via restaurant tooling). |
| `restaurant_users` | Owners add staff/owners via invite codes. | Owners and staff see staff list within the same restaurant. | Owners manage roles/status; staff cannot update others. | Only owners can remove staff/owners. |
| `invite_codes` | Only owners can generate invite codes for their restaurant. | Only owners can list invite codes created for their restaurant. | Only owners can move invite codes to another restaurant. | Only owners can soft-delete invite codes. |
| `suppliers` | Owners/staff can add standalone suppliers via file uploads (price/invoice ingestion). | Owners and staff see the suppliers linked to their outlets. | Owners and staff can update supplier metadata for linked outlets. | Only owners can deactivate suppliers for their outlet. |
| `restaurant_suppliers` | Owners/staff can link a supplier to an outlet through the interface. | Owners and staff view every supplier link for their restaurant. | Only owners can change link status or metadata. | Only owners can unlink/disassociate a supplier from their restaurant. |
| `supplier_items` | Owners/staff upload supplier items via file-processing flows. | Owners/staff can read supplier items tied to their outlets. | Uploaded data updates supplier items; owners/staff cannot directly edit manually. | Handled by ingestion (not user-deletable). |
| `supplier_prices` | Added via uploads/pricing files by staff/owners. | Staff/owners read prices for their linked supplier items. | Updates come from price list uploads. | Managed by file-processing; no manual delete. |
| `products` | Created/updated automatically when supplier items map; staff/owners cannot manually create. | Staff/owners read products tied to their restaurants. | System-managed; owners/staff cannot manually update core product rows. | System-managed; not user-deletable. |
| `invoices` | Owners/staff create invoices via file uploads. | Owners/staff can read invoices for their restaurant. | Updates happen through invoice upload workflow. | File-processing handles closures; owners/staff cannot delete arbitrarily. |
| `invoice_line_items` | Imported alongside invoices by owners/staff. | Readable by same owners/staff. | Managed via upload tooling; no manual edits. | System-managed. |

### Internal / unused tables
| Table | Notes |
| --- | --- |
| `telegram_sessions` | Active internal table (Telegram session tracking). |
| `telegram_messages` | Active internal table (incoming Telegram message logs). |
| `telegram_outgoing_messages` | Active internal table (outgoing Telegram message logs). |
| `llm_calls` | Active internal table (LLM telemetry/cost tracking). |
| `processing_events` | Active internal table (system processing event logs). |
| `price_comparisons` | Active internal table (internal pricing comparison records). |
| `supplier_item_products` | Not currently exposed; reserved for upstream mappings. |
| `supplier_disputes` | Tracking field not surfaced in current UI. |
| `product_aliases` | Auto-generated alias data; no customer-facing flows yet. |
| `inventory_locations` | Inventory feature is inactive; table unused. |
| `inventory_batches` | Inventory feature is inactive; table unused. |
| `inventory_movements` | Inventory feature is inactive; table unused. |
| `user_upload_limits` | Internal rate limits for uploads; not exposed. |
| `documents` | File metadata; not meant for direct user CRUD. |
| `file_processing_runs` | Internal processing telemetry (already in active list but considered internal). |
| `file_processing_steps` | Internal workflow steps. |
| `file_processing_staging` | Internal staging area for uploads. |
| `file_processing_page_jobs` | Internal job tracking for page extraction. |
| `file_processing_payloads` | Internal payload storage for file ingestion. |

### Tools (functions)
| Tool | What it does | Required args | Optional args | Notes |
| --- | --- | --- | --- | --- |
| `check_file_processing_status` | Check the status of a file processing job. Use this when user asks 'how's my file?', 'what's the status?', or similar questions. | — | run_id: string — The file processing run UUID (optional - if not provided, returns latest job for user). | Internal (file-processing workflow) |
| `confirm_file_processing` | Confirm and write extracted data to final tables. Both owners and staff can confirm (attribution is tracked). | staging_id: string — The file processing staging record UUID. | — | Internal (file-processing workflow) |
| `create_inventory_movement` | Record an inventory movement (receive, consume, waste, adjust). | batch_id: string — The inventory batch's UUID.<br>movement_type: string — Type of movement.<br>quantity: number — Quantity for this movement. | reason: string — Reason for the movement (optional). | Inventory (currently not used in UI) |
| `create_invite_code` | Create an invite link to add staff to your restaurant. Only owners can create invites. | restaurant_id: string — The restaurant's UUID. | role: string — Role for the invitee: 'staff' or 'owner'. Defaults to 'staff'.<br>expires_in_days: integer — Days until the invite expires. Defaults to 30. | — |
| `create_restaurant` | Create a new restaurant/outlet. You will become the owner. | name: string — Name for the new restaurant. | — | — |
| `create_supplier` | Create a new supplier for a restaurant. | restaurant_id: string — The restaurant's UUID.<br>name: string — Supplier name. | currency: string — Currency code (e.g., USD, EUR) (optional).<br>language: string — Language code (optional).<br>lead_time_days: integer — Lead time in days (optional).<br>notes: string — Additional notes (optional).<br>account_number: string — Account number (optional). | — |
| `delete_invite_code` | Delete/revoke an unused invite code. Only owners can delete. | code: string — The invite code to delete. | — | — |
| `find_restaurant_by_name` | Search for a restaurant by name. Use this when user mentions a restaurant name. | name: string — The restaurant name to search for. | — | — |
| `get_current_datetime` | Get the current UTC date and time in ISO-8601 format. | — | — | — |
| `get_help_topic` | Get help text for a specific topic (profile, outlets, staff, suppliers, invoices, inventory, files, invites) or the main menu if topic is empty. | — | topic: string — Optional help topic name. | — |
| `get_inventory_batch` | Get details of a specific inventory batch by ID. | batch_id: string — The inventory batch's UUID. | — | Inventory (currently not used in UI) |
| `get_menu_paths` | List the available bot paths with short descriptions and example questions. | — | — | — |
| `get_my_profile` | Get your profile information (name, phone, username). | — | — | — |
| `get_restaurant` | Get details of a specific restaurant by ID. | restaurant_id: string — The restaurant's UUID. | — | — |
| `get_supplier` | Get details of a specific supplier by ID. | supplier_id: string — The supplier's UUID. | restaurant_id: string — Restaurant UUID for scoped details (optional). | — |
| `link_suppliers` | Link supplier(s) to outlet(s). Supports 1→1, 1→many, many→many, many→1, and all outlets. | — | supplier_id: string — Single supplier UUID (optional).<br>supplier_ids: array — List of supplier UUIDs (optional).<br>supplier_name: string — Single supplier name (optional).<br>supplier_names: array — List of supplier names (optional).<br>restaurant_id: string — Single outlet UUID (optional).<br>restaurant_ids: array — List of outlet UUIDs (optional).<br>restaurant_name: string — Single outlet name (optional).<br>restaurant_names: array — List of outlet names (optional).<br>all_outlets: boolean — If true, links to all outlets you can access.<br>status_value: string — Link status: active or inactive (optional).<br>account_number: string — Account number to set on the link(s) (optional).<br>default_currency: string — Default currency to set on the link(s) (optional).<br>currency: string — Alias for default_currency (optional).<br>language: string — Supplier language to set if missing (optional).<br>lead_time_days: integer — Lead time in days to set on the link(s) (optional).<br>notes: string — Notes to set on the link(s) (optional). | — |
| `list_inventory` | List inventory batches for a restaurant. Returns JSON with restaurant_name and batches. | restaurant_id: string — The restaurant's UUID. | product_id: string — Product UUID to filter by (optional). | Inventory (currently not used in UI) |
| `list_invite_codes` | List active invite codes for a restaurant. Returns JSON with restaurant_name and invites. | restaurant_id: string — The restaurant's UUID. | — | — |
| `list_my_restaurants` | List all restaurants/outlets you own or have access to. | — | — | — |
| `list_my_suppliers` | List all suppliers across your outlets (and any you added but haven't linked yet), including legacy suppliers with NULL user_id that are linked to your outlets. Returns JSON. | — | — | — |
| `list_staff` | List all staff members of a restaurant. | restaurant_id: string — The restaurant's UUID. | — | — |
| `list_suppliers` | List suppliers linked to a specific outlet/restaurant. Returns JSON with restaurant_name and suppliers. | restaurant_id: string — The restaurant's UUID. | — | — |
| `list_unlinked_suppliers` | List your suppliers that are not linked to an outlet (or not linked to a specific outlet if restaurant_id provided). Returns JSON. | — | restaurant_id: string — Optional restaurant/outlet UUID to show suppliers not linked to that outlet. | — |
| `process_inventory_photo` | Process an inventory photo. The photo will be analyzed and you'll get a preview to review before saving. | restaurant_id: string — The restaurant's UUID.<br>file_id: string — Telegram file_id of the inventory photo. | — | Internal (file-processing workflow) |
| `process_invoice_file` | Process an invoice file. The file will be analyzed and you'll get a preview to review before saving. | restaurant_id: string — The restaurant's UUID.<br>file_id: string — Telegram file_id of the invoice file. | supplier_id: string — Supplier UUID (optional, will be inferred if not provided). | Internal (file-processing workflow) |
| `process_price_list_file` | Process a price list file. The file will be analyzed and you'll get a preview to review before saving. | restaurant_id: string — The restaurant's UUID.<br>file_id: string — Telegram file_id of the price list file. | supplier_id: string — Supplier UUID (optional, will be inferred if not provided). | Internal (file-processing workflow) |
| `review_file_processing` | Show extracted data from file processing for review. | staging_id: string — The file processing staging record UUID. | — | Internal (file-processing workflow) |
| `revoke_staff_access` | Remove a user from restaurant staff. Only owners can do this. | restaurant_id: string — The restaurant's UUID.<br>user_id: string — The user's UUID to remove. | — | — |
| `update_file_processing_data` | Update a specific field in the extracted data before confirming. Use dot notation for nested fields (e.g., 'supplier_name', 'line_items.0.quantity'). | staging_id: string — The file processing staging record UUID.<br>field_path: string — Path to the field to update (e.g., 'supplier_name', 'line_items.0.quantity').<br>new_value: any — New value for the field. | — | Internal (file-processing workflow) |
| `update_missing_field` | Update a missing supplier or currency field when the system asks for it. Use this when the user responds to a missing field question. | staging_id: string — The file processing staging record UUID.<br>value: string — The supplier name or currency value provided by the user. | — | Internal (file-processing workflow) |
| `update_my_profile` | Update your profile information. Only include fields the user explicitly provided. Use empty string only when the user asks to clear a field. | — | full_name: string — Your full name. Pass empty string to clear.<br>phone: string — Your phone number. Pass empty string to clear.<br>username: string — Your username (without @). Pass empty string to clear. | — |
| `update_restaurant` | Update restaurant details. Only owners can update. | restaurant_id: string — The restaurant's UUID.<br>name: string — New name for the restaurant. | — | — |
| `update_supplier` | Update supplier details for a supplier. | supplier_id: string — The supplier's UUID. | restaurant_id: string — Restaurant UUID for scoped updates (optional).<br>name: string — Supplier name (optional).<br>currency: string — Currency code (e.g., USD, EUR) (optional).<br>language: string — Language code (optional).<br>lead_time_days: integer — Lead time in days (optional).<br>notes: string — Additional notes (optional).<br>account_number: string — Account number (optional).<br>status: string — Restaurant supplier status (optional).<br>is_active: boolean — Whether the supplier is active (optional). | — |
