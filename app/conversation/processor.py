"""
Instant message processor for the intent-driven bot.

Processes messages immediately without batching:
1. Send instant ACK
2. Load user context
3. Classify intent
4. Execute operation
5. Send response
6. Update context
7. Record telemetry
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent, classify_intent
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.conversation import responses
from app.conversation.context import (
    load_context,
    update_context_from_result,
    get_context_for_classifier,
)
from app.conversation.executor import execute_intent, UserContext
from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.bot_api import send_message
from app.workers.telemetry import record_llm_call, record_outgoing_message

logger = logging.getLogger(__name__)


def _get_message_text(messages: list[TelegramMessages]) -> str:
    """Extract text from telegram messages."""
    parts = []
    for msg in messages:
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
    return "\n".join(parts).strip()


def _has_file(messages: list[TelegramMessages]) -> tuple[bool, str | None, str | None]:
    """Check if messages contain a file and return file info."""
    for msg in messages:
        if msg.file_id:
            return True, msg.file_kind, msg.file_id
    return False, None, None


def _create_closed_session(
    db: Session,
    chat_id: int,
) -> TelegramSessions:
    """Create a closed session for logging purposes."""
    now = dt.datetime.now(dt.UTC)
    session = TelegramSessions(
        chat_id=chat_id,
        started_at=now,
        last_activity_at=now,
        flush_at=now,
        status="closed",
        hint_command=None,
        closed_at=now,
        ack_sent_at=None,
    )
    db.add(session)
    db.flush()
    return session


def process_message_instant(
    *,
    db: Session,
    user: User,
    messages: list[TelegramMessages],
    settings: Settings,
    session_id: uuid.UUID | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """
    Process user message instantly and return response.

    This is the main entry point for the new intent-driven bot.
    Called directly from handler.py without batching.

    Args:
        db: Database session
        user: Current user
        messages: Telegram messages to process
        settings: App settings
        session_id: Session ID for telemetry (optional)
        history: Recent conversation history for context

    Returns:
        Response text to send to user
    """
    chat_id = user.chat_id
    message_text = _get_message_text(messages)
    has_file, file_kind, file_id = _has_file(messages)

    # Create session for telemetry if not provided
    if session_id is None:
        session_row = _create_closed_session(db, chat_id)
        session_id = session_row.id
        db.commit()

    # Load user context
    context = load_context(db, user)
    classifier_context = get_context_for_classifier(context)

    # Record start time
    started_at = dt.datetime.now(dt.UTC)

    # Handle file uploads specially - detect type with vision
    if has_file and file_id:
        return _handle_file_upload(
            db=db,
            user=user,
            context=context,
            file_id=file_id,
            file_kind=file_kind,
            message_text=message_text,
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
        )

    # Classify intent
    classified = classify_intent(
        message_text=message_text or "",
        settings=settings,
        has_file=has_file,
        file_kind=file_kind,
        context=classifier_context,
        history=history,
    )

    # Record LLM call for telemetry
    if classified.usage:
        record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="intent_classification",
            model=classified.model,
            openrouter_generation_id=classified.generation_id,
            upstream_id=None,
            provider_name=None,
            usage=classified.usage,
            latency_ms=classified.latency_ms,
            total_cost_usd=None,
            error=None,
        )
        db.commit()

    logger.info(
        "intent_classified",
        extra={
            "intent": classified.intent.value,
            "confidence": classified.confidence,
            "params": classified.params,
            "chat_id": chat_id,
            "user_id": str(user.id),
        },
    )

    # Execute the intent
    result = execute_intent(
        classified=classified,
        db=db,
        user=user,
        context=context,
        settings=settings,
    )

    # Update context based on result
    if result.context_update:
        context = update_context_from_result(
            db=db,
            user=user,
            context=context,
            context_update=result.context_update,
        )

    # Update user's last interaction time
    user.last_interaction_at = dt.datetime.now(dt.UTC)
    db.commit()

    # Log processing event
    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="instant_processed_v1",
            payload_json={
                "intent": classified.intent.value,
                "confidence": classified.confidence,
                "success": result.success,
            },
            error=None,
        )
    )
    db.commit()

    return result.response


def _handle_file_upload(
    *,
    db: Session,
    user: User,
    context: UserContext,
    file_id: str,
    file_kind: str | None,
    message_text: str,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
) -> str:
    """
    Handle file upload with vision-based type detection.

    Flow:
    1. Detect file type using vision LLM (price list vs invoice)
    2. Queue file processing
    3. Return appropriate response

    For now, we'll use a simplified approach that queues the file
    and lets the existing file processing workers handle it.
    """
    from app.workers.tasks import (
        process_price_list_file_task,
        process_invoice_file_task,
    )
    from app.workers.celery_types import CeleryApplyAsync
    from typing import cast

    # Determine file type from caption/context
    text_lower = (message_text or "").lower()

    is_price_list = any(
        kw in text_lower
        for kw in ["price list", "pricelist", "prices", "rate card", "catalog"]
    )
    is_invoice = any(
        kw in text_lower
        for kw in ["invoice", "bill", "receipt", "challan"]
    )

    # Get restaurant ID
    restaurants = _get_user_restaurants(db, user.id)
    if not restaurants:
        return responses.ERROR_NO_OUTLETS

    # Auto-select if only one, otherwise use from context
    restaurant_id = context.active_restaurant_id
    if not restaurant_id and len(restaurants) == 1:
        restaurant_id = restaurants[0]["id"]

    if not restaurant_id:
        # Need to ask which outlet
        # Store file info in context for later
        context.staging_id = None  # Will be set after processing
        context.collected_params = {
            "file_id": file_id,
            "file_kind": file_kind,
            "detected_type": "price_list" if is_price_list else ("invoice" if is_invoice else "unknown"),
        }
        context.active_operation = "file_upload"
        context.pending_params = ["restaurant_id"]
        _save_context(db, user, context)

        return responses.outlet_select_prompt(restaurants)

    # Queue the appropriate processing task
    task_kwargs = {
        "restaurant_id": restaurant_id,
        "file_id": file_id,
        "supplier_id": None,
        "chat_id": chat_id,
        "user_id": str(user.id),
        "session_id": str(session_id),
    }

    if is_price_list:
        cast(CeleryApplyAsync, process_price_list_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_DETECTED_PRICE_LIST
    elif is_invoice:
        cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_DETECTED_INVOICE
    else:
        # Can't determine type - default to invoice
        cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_PROCESSING_STARTED.format(file_type="file")


def _get_user_restaurants(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get list of restaurants user has access to."""
    from app.domain.services.restaurant_service import RestaurantService

    service = RestaurantService(db)
    rows = service.list_for_user(user_id=user_id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "role": m.role,
        }
        for r, m in rows
    ]


def _save_context(db: Session, user: User, context: UserContext) -> None:
    """Save context to user."""
    user.state_data = context.to_dict()
    db.add(user)


def send_ack_message(
    *,
    chat_id: int,
    settings: Settings,
    has_file: bool = False,
) -> int | None:
    """
    Send instant acknowledgment message.

    Returns the Telegram message ID or None if sending failed.
    """
    text = responses.ACK_FILE_PROCESSING if has_file else responses.ACK_PROCESSING
    try:
        return send_message(chat_id=chat_id, text=text, settings=settings)
    except Exception as e:
        logger.warning(
            "ack_send_failed",
            extra={"chat_id": chat_id, "error": str(e)},
        )
        return None
