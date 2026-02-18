"""Priority 4: File upload handler.

Called when a user sends a document, photo, or other file.
Creates a staging record and asks the user what type of document it is.

Flow:
    User sends file
    → create staging record (status=processing)
    → set active_staging_id in context
    → ask "Invoice or Price List?" with inline keyboard
    → doc_type button handler dispatches the Celery OCR task
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.staging_service import StagingService
from app.telegram.bot_api import send_message
from app.telegram.keyboards import doc_type_keyboard
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Handle an incoming file/photo upload."""
    message = update.get("message") or {}
    document = message.get("document")
    photo = message.get("photo")

    # Extract file info
    if document:
        file_id = document.get("file_id", "")
        file_unique_id = document.get("file_unique_id", "")
        mime = document.get("mime_type") or "application/octet-stream"
    elif photo:
        # photo is a list of sizes — take the largest one
        largest = max(photo, key=lambda p: p.get("file_size", 0))
        file_id = largest.get("file_id", "")
        file_unique_id = largest.get("file_unique_id", "")
        mime = "image/jpeg"
    else:
        # No recognisable file
        send_message(
            chat_id=user.chat_id,
            text="Please send a document or image file.",
            settings=settings,
        )
        return

    if not file_id:
        logger.warning("files.handle: no file_id in update")
        send_message(
            chat_id=user.chat_id,
            text="Could not read file. Please try again.",
            settings=settings,
        )
        return

    # Require active restaurant
    restaurant_id = ctx_svc.get_active_restaurant_id(user)
    if restaurant_id is None:
        send_message(
            chat_id=user.chat_id,
            text="Please select a restaurant first. Use /start to get started.",
            settings=settings,
        )
        return

    # Create staging record
    staging_svc = StagingService(db)
    staging = staging_svc.create(
        uploaded_by=user.id,
        restaurant_id=restaurant_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        mime=mime,
        session_id=None,  # session_id optional; populated by task layer if needed
    )

    # Store active staging ID in user context
    ctx_svc.set_active_staging(user, staging.id)
    db.commit()

    logger.info(
        "file_received staging_id=%s mime=%s user_id=%s",
        staging.id,
        mime,
        user.id,
    )

    # Ask user what type of document this is
    _send_doc_type_question(
        chat_id=user.chat_id,
        staging_id=staging.id,
        settings=settings,
    )


def _send_doc_type_question(chat_id: int, staging_id, settings: Settings) -> None:
    """Send "What type of document?" with inline keyboard."""
    keyboard = doc_type_keyboard(staging_id)
    text = "📎 File received! What type of document is this?"

    if not settings.telegram_bot_token:
        # Dev fallback — plain message
        send_message(chat_id=chat_id, text=text, settings=settings)
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": keyboard,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("files: failed to send doc_type keyboard: %s", exc)
        # Fall back to plain message
        send_message(
            chat_id=chat_id,
            text=f"{text}\n\nReply with 'invoice' or 'pricelist'.",
            settings=settings,
        )
