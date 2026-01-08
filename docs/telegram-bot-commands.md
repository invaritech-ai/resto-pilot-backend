# Telegram bot commands (Bot API)

## Set the command menu

```bash
curl -sS -X POST "https://api.telegram.org/bot$APP_TELEGRAM_BOT_TOKEN/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{"commands":[
    {"command":"start","description":"Start / register"},
    {"command":"menu","description":"Show main menu"},
    {"command":"help","description":"Show help / menu"},
    {"command":"confirm","description":"Confirm a pending upload"},
    {"command":"cancel","description":"Cancel current operation"}
  ]}'
```

## Get the current command menu

```bash
curl -sS "https://api.telegram.org/bot$APP_TELEGRAM_BOT_TOKEN/getMyCommands"
```
