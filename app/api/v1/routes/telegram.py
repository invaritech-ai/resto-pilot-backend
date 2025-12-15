import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.telegram.processor import process_update

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    update = await request.json()
    logger.info("telegram_webhook_received", extra={"update": update})

    response = process_update(update=update, session=db, settings=get_settings())
    if response is None:
        return JSONResponse({"status": "ok"})
    return JSONResponse(response)
