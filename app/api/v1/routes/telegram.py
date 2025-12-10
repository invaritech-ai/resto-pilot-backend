import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/telegram")
async def telegram_webhook(request: Request):
    update = await request.json()  # <- This gets the full Telegram update
    logger.info("telegram_webhook_received", extra={"update": update})

    # TODO: pass update to your telegram handler
    # response = await process_update(update)

    # Echo back to Telegram directly in the webhook response.
    # Telegram will execute this single API call on your behalf.
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text") or ""

    if chat_id and text:
        logger.info("telegram_echo_sent_via_webhook_response", extra={"chat_id": chat_id})
        return JSONResponse(
            {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": f"Echo: {text}",
            }
        )
    else:
        logger.warning(
            "telegram_echo_skipped",
            extra={
                "chat_id_present": bool(chat_id),
                "text_present": bool(text),
            },
        )

    return JSONResponse({"status": "ok"})
