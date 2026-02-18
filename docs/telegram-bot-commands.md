# Telegram Commands

## Commands currently supported by the bot
- `/start`
- `/help`
- `/profile`
- `/team`
- `/outlets`
- `/products`
- `/prices <supplier name>`
- `/inventory`
- `/balance`
- `/uploads`
- `/list suppliers`
- `/add supplier <name>`
- `/link supplier <name>`

## Important note about BotFather command menus
Telegram command menus only support single-token commands (no spaces), so these phrase-style commands cannot be represented exactly in BotFather:
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
    {"command":"profile","description":"Your profile"},
    {"command":"team","description":"Team members"},
    {"command":"outlets","description":"Your restaurant"},
    {"command":"products","description":"Supplier product catalog"},
    {"command":"prices","description":"Prices from a supplier"},
    {"command":"inventory","description":"Stock levels"},
    {"command":"balance","description":"Stock summary"},
    {"command":"uploads","description":"Pending uploads"}
  ]}'
```
