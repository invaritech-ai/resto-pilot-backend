# Telegram Commands

## Commands currently supported by the bot

### Inventory
- `/inventory` — Stock levels (with `[+]`/`[-]` quick adjust buttons per row)
- `/balance` — Stock summary (zero and negative balance alerts)
- `/chart` — Visual stock levels bar chart (per-unit-group, seaborn/PNG)
- `/history <item name>` — Last 10 transactions for a specific item
- `/export` — Download inventory as CSV file

### Suppliers & Prices
- `/list suppliers` — List all linked suppliers
- `/add supplier <name>` — Add a new supplier (link if exists, create if not)
- `/link supplier <name>` — Link an existing global supplier to your restaurant
- `/products` — Supplier product catalog (paginated)
- `/prices <supplier name>` — Prices from a supplier (also accepts item-first search)

### Search
- `/search <query>` — Unified cross-domain search across inventory items, supplier products, and supplier names

### Uploads
- `/uploads` — Pending upload reviews

### Account
- `/profile` — Your profile
- `/team` — Team members
- `/outlets` — Your restaurant(s)

### General
- `/start` — Start or reset
- `/help` — Show all commands

## Free-text stock shortcuts

The bot also recognises these natural language patterns without any `/` prefix:

| Pattern | Action |
|---|---|
| `"used 1kg onion"` | Debit: record usage with confirm keyboard |
| `"use 500g tomato"` | Debit: same as above |
| `"2kg chicken left"` | Set balance: reconcile to target |
| `"5 lettuce remaining"` | Set balance: same (no unit) |

Both patterns show the item match, current → projected balance, and a confirm/cancel keyboard before writing.

## Important note about BotFather command menus

Telegram command menus only support single-token commands (no spaces), so phrase-style commands cannot be represented exactly in BotFather:
- `/list suppliers`
- `/add supplier <name>`
- `/link supplier <name>`

If you set a BotFather menu, include only single-token commands and keep `/help` as the canonical full command reference.

## Example `setMyCommands`
```bash
curl -sS -X POST "https://api.telegram.org/bot$APP_TELEGRAM_BOT_TOKEN/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{"commands":[
    {"command":"start","description":"Start or reset"},
    {"command":"help","description":"Show all commands"},
    {"command":"inventory","description":"Stock levels"},
    {"command":"balance","description":"Stock summary"},
    {"command":"chart","description":"Visual stock chart"},
    {"command":"history","description":"Item transaction history"},
    {"command":"export","description":"Download inventory CSV"},
    {"command":"search","description":"Search inventory, products & suppliers"},
    {"command":"products","description":"Supplier product catalog"},
    {"command":"prices","description":"Prices from a supplier"},
    {"command":"uploads","description":"Pending uploads"},
    {"command":"profile","description":"Your profile"},
    {"command":"team","description":"Team members"}
  ]}'
```
