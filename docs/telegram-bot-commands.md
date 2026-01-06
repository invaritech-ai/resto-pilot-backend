# Telegram bot commands (Bot API)

## Set the command menu

```bash
curl -sS -X POST "https://api.telegram.org/bot$APP_TELEGRAM_BOT_TOKEN/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{"commands":[
    {"command":"start","description":"Start / register"},
    {"command":"respond","description":"Force process current session"},
    {"command":"done","description":"Finish and process"},
    {"command":"confirm","description":"Confirm pending action"},
    {"command":"cancel","description":"Cancel pending action"}
  ]}'
```

## Get the current command menu

```bash
curl -sS "https://api.telegram.org/bot$APP_TELEGRAM_BOT_TOKEN/getMyCommands"
```
