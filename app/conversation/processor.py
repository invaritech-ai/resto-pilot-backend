"""
Instant message processor for the intent-driven bot.

Simplified flow:
1. Load user context
2. Resolve intent + call tools via a single LLM loop
3. Send final response
4. Update context
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent
from app.ai.tool_resolver import resolve_with_tools
from app.conversation import responses
from app.conversation.context import load_context, update_context_from_result
from app.conversation.executor import UserContext
from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.domain.services.restaurant_service import RestaurantService
from app.telegram.bot_api import send_message
from app.workers.telemetry import record_llm_call, record_outgoing_message

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    response_text: str
    response_llm_call_id: uuid.UUID | None = None



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


def _decode_unicode_escapes(text: str) -> str:
    if "\\u" not in text:
        return text
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    except Exception:
        return text



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


def _get_user_restaurants(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get list of restaurants user has access to."""
    service = RestaurantService(db)
    rows = service.list_for_user(user_id=user_id)
    return [
        {"id": str(r.id), "name": r.name, "role": m.role}
        for r, m in rows
    ]


def process_message_instant(
    *,
    db: Session,
    user: User,
    messages: list[TelegramMessages],
    settings: Settings,
    session_id: uuid.UUID | None = None,
    history: list[dict[str, str]] | None = None,
) -> ProcessResult:
    """
    Process user message instantly and return response metadata.

    This is the main entry point for the intent-driven bot.
    Called directly from handler.py without batching.

    Args:
        db: Database session
        user: Current user
        messages: Telegram messages to process
        settings: App settings
        session_id: Session ID for telemetry (optional)
        history: Recent conversation history for context

    Returns:
        ProcessResult with response text and associated LLM call ID (if any)
    """
    chat_id = user.chat_id
    message_text = _get_message_text(messages)
    has_file, file_kind, file_id = _has_file(messages)

    # Create session for telemetry if not provided
    if session_id is None:
        session_row = _create_closed_session(db, chat_id)
        session_id = session_row.id
        db.commit()
    assert session_id is not None

    # Load user context
    context = load_context(db, user)

    # Record start time
    started_at = dt.datetime.now(dt.UTC)

    # Handle file uploads specially - detect type with vision
    if has_file and file_id:
        upload_response, upload_intent = _handle_file_upload(
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

        response_llm_call_id = None
        final_text = upload_response

        user.last_interaction_at = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_id,
                at=dt.datetime.now(dt.UTC),
                event="file_upload_processed_v2",
                payload_json=json.dumps({
                    "intent": upload_intent.value,
                    "success": True,
                }),
                error=None,
            )
        )
        db.commit()
        return ProcessResult(
            response_text=final_text,
            response_llm_call_id=response_llm_call_id,
        )

    tool_result = resolve_with_tools(
        db=db,
        user=user,
        message_text=message_text or "",
        history=history,
        active_restaurant_id=context.active_restaurant_id,
        active_supplier_id=context.active_supplier_id,
        settings=settings,
        chat_id=chat_id,
        session_id=session_id,
    )

    response_llm_call_id = None
    for call in tool_result.llm_calls:
        response_llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="tool_resolution",
            model=call.model,
            openrouter_generation_id=call.generation_id,
            usage=call.usage,
            latency_ms=call.latency_ms,
        )
    if tool_result.llm_calls:
        db.commit()

    if tool_result.context_update:
        context = update_context_from_result(
            db=db,
            user=user,
            context=context,
            context_update=tool_result.context_update,
        )

    final_text = tool_result.response_text

    # Update user's last interaction time
    user.last_interaction_at = dt.datetime.now(dt.UTC)
    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="tool_response_generated_v1",
            payload_json=json.dumps({
                "tool_calls": tool_result.tool_calls,
                "llm_calls": len(tool_result.llm_calls),
            }),
            error=None,
        )
    )
    db.commit()

    return ProcessResult(
        response_text=final_text,
        response_llm_call_id=response_llm_call_id,
    )


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
) -> tuple[str, Intent]:
    """
    Handle file upload with type detection.

    Flow:
    1. Detect file type from caption/context
    2. Queue file processing
    3. Return appropriate response
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

    intent_guess = Intent.UPLOAD_PRICE_LIST if is_price_list else Intent.UPLOAD_INVOICE

    # Get restaurant ID
    restaurants = _get_user_restaurants(db, user.id)
    if not restaurants:
        return responses.ERROR_NO_OUTLETS, intent_guess

    # Auto-select if only one, otherwise use from context
    restaurant_id = context.active_restaurant_id
    if not restaurant_id and len(restaurants) == 1:
        restaurant_id = restaurants[0]["id"]

    if not restaurant_id:
        return responses.outlet_select_prompt(restaurants), intent_guess

    # Get supplier_id from context if available
    supplier_id = context.active_supplier_id

    # Queue the appropriate processing task
    task_kwargs = {
        "restaurant_id": restaurant_id,
        "file_id": file_id,
        "supplier_id": supplier_id,
        "chat_id": chat_id,
        "user_id": str(user.id),
        "session_id": str(session_id),
    }

    if is_price_list:
        cast(CeleryApplyAsync, process_price_list_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_DETECTED_PRICE_LIST, intent_guess
    elif is_invoice:
        cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_DETECTED_INVOICE, intent_guess
    else:
        # Can't determine type - default to invoice
        cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_PROCESSING_STARTED.format(file_type="file"), intent_guess


def send_ack_message(
    *,
    chat_id: int,
    settings: Settings,
    has_file: bool = False,
    file_kind: str | None = None,
    message_text: str = "",
    db: Session | None = None,
    session_id: uuid.UUID | None = None,
) -> int | None:
    """
    Send instant acknowledgment message.

    Uses static ACK messages instead of LLM generation.

    Returns the Telegram message ID or None if sending failed.
    """
    text = responses.ACK_FILE_PROCESSING if has_file else responses.ACK_PROCESSING
    
    try:
        telegram_message_id = send_message(chat_id=chat_id, text=text, settings=settings)

        # Record ACK for telemetry if db session provided
        if db is not None and session_id is not None and telegram_message_id is not None:
            record_outgoing_message(
                db=db,
                session_id=session_id,
                chat_id=chat_id,
                kind="ack",
                text=text,
                telegram_message_id=telegram_message_id,
                llm_call_id=None,
            )

        return telegram_message_id
    except Exception as e:
        logger.warning(
            "ack_send_failed",
            extra={"chat_id": chat_id, "error": str(e)},
        )
        return None
