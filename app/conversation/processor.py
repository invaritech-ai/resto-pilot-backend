"""
Instant message processor for the intent-driven bot.

Processes messages immediately without batching:
1. Send instant ACK
2. Load user context
3. Classify intent
4. Resolve intent with decision LLM
5. Execute operation
6. Generate response with response LLM
7. Update context
8. Record telemetry
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent, ClassifiedIntent, classify_intent
from app.ai.model_config import get_decision_model, get_response_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
    create_chat_completion_text_allow_empty_with_http_info,
)
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
from app.db.models.suppliers import Suppliers
from app.domain.services.restaurant_service import RestaurantService
from app.telegram.bot_api import send_message
from app.workers.telemetry import record_llm_call, record_outgoing_message

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    response_text: str
    response_llm_call_id: uuid.UUID | None = None


@dataclass
class DecisionResult:
    intent: Intent
    params: dict[str, Any]
    confidence: float
    reason: str | None = None
    model: str = ""
    latency_ms: int = 0
    generation_id: str | None = None
    usage: dict[str, Any] | None = None


DECISION_SYSTEM_PROMPT = """You are an intent resolver for a restaurant management bot.
Use the classifier output as a hint, but you may override it if the user's message,
history, and entity candidates clearly support a different intent.

Rules:
- Choose one intent from the allowed list provided in the user prompt.
- IDs are internal; users will not provide them. Never ask for IDs.
- Use candidate entities to map names to IDs when possible.
- If you cannot resolve an entity, keep the raw name in params and ask for clarification in reason.
- Return ONLY valid JSON with: intent, params, confidence, reason.
"""

RESPONSE_SYSTEM_PROMPT = """You are a response composer for a restaurant management bot.
Use only the provided action result and user message. Do not invent facts.
Keep responses short, informative, and non-technical (1-3 sentences).
If the action result contains a structured list or formatted block, you may return it unchanged.
"""


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


def _format_history_for_prompt(
    history: list[dict[str, str]] | None,
    limit: int = 20,
) -> str | None:
    if not history:
        return None

    lines: list[str] = []
    for msg in history[-limit:]:
        role = msg.get("role", "")
        content = msg.get("content", "")[:200]
        if role and content:
            lines.append(f"{role}: {content}")
    if not lines:
        return None
    return "Recent conversation:\n" + "\n".join(lines)


def _build_decision_candidates(
    db: Session,
    user: User,
    context: UserContext,
    max_suppliers: int = 50,
    max_staff: int = 50,
) -> dict[str, Any]:
    restaurants = _get_user_restaurants(db, user.id)
    candidates: dict[str, Any] = {"restaurants": restaurants}

    active_restaurant_id = context.active_restaurant_id
    if not active_restaurant_id:
        return candidates

    try:
        restaurant_uuid = uuid.UUID(active_restaurant_id)
    except (ValueError, AttributeError):
        return candidates

    suppliers = db.scalars(
        select(Suppliers)
        .where(
            Suppliers.restaurant_id == restaurant_uuid,
            Suppliers.is_active == True,
        )
        .order_by(Suppliers.name.asc())
        .limit(max_suppliers)
    ).all()
    candidates["suppliers"] = [{"id": str(s.id), "name": s.name} for s in suppliers]

    members = RestaurantService(db).list_members(restaurant_id=restaurant_uuid)
    staff_list = []
    for member, membership in members[:max_staff]:
        staff_list.append(
            {
                "id": str(member.id),
                "name": member.full_name,
                "username": member.username,
                "role": membership.role,
            }
        )
    candidates["staff"] = staff_list
    return candidates


def _resolve_intent_with_llm(
    *,
    db: Session,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
    message_text: str,
    classified: ClassifiedIntent,
    context: dict[str, Any] | None,
    history: list[dict[str, str]] | None,
    candidates: dict[str, Any],
) -> tuple[DecisionResult | None, uuid.UUID | None]:
    intent_values = [intent.value for intent in Intent]
    user_prompt_parts = [
        f"Allowed intents: {intent_values}",
        f"Classifier result: {json.dumps({'intent': classified.intent.value, 'params': classified.params, 'confidence': classified.confidence})}",
    ]

    if context:
        user_prompt_parts.append(f"Active context:\n{json.dumps(context, indent=2)}")

    history_text = _format_history_for_prompt(history, limit=20)
    if history_text:
        user_prompt_parts.append(history_text)

    if candidates:
        user_prompt_parts.append(f"Entity candidates:\n{json.dumps(candidates, indent=2)}")

    user_prompt_parts.append(f"User message: {message_text}")
    user_prompt = "\n\n".join(user_prompt_parts)

    model = get_decision_model(settings)
    decision_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=decision_settings,
            messages=[
                {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except OpenAIError as exc:
        logger.exception("intent_resolution_failed", extra={"error": str(exc)})
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="intent_resolution",
            model=model,
            error=str(exc),
        )
        db.commit()
        return None, llm_call_id

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", model)

    content: str | None = None
    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Empty content")
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.startswith("```"))

        parsed = json.loads(content)
        intent_value = parsed.get("intent")
        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        confidence = float(parsed.get("confidence", 0.0))
        reason = parsed.get("reason")

        intent = Intent(intent_value) if intent_value in intent_values else None
        if intent is None:
            raise ValueError("Invalid intent")

        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="intent_resolution",
            model=model_used if isinstance(model_used, str) else model,
            openrouter_generation_id=generation_id,
            usage=usage,
            latency_ms=latency_ms,
        )
        db.commit()

        return (
            DecisionResult(
                intent=intent,
                params=params,
                confidence=confidence,
                reason=reason if isinstance(reason, str) else None,
                model=model_used if isinstance(model_used, str) else model,
                latency_ms=latency_ms,
                generation_id=generation_id,
                usage=usage if isinstance(usage, dict) else None,
            ),
            llm_call_id,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "intent_resolution_parse_failed",
            extra={"error": str(exc), "content": content},
        )
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="intent_resolution",
            model=model_used if isinstance(model_used, str) else model,
            openrouter_generation_id=generation_id,
            usage=usage,
            latency_ms=latency_ms,
            error=str(exc),
        )
        db.commit()
        return None, llm_call_id


def _render_response_with_llm(
    *,
    db: Session,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
    message_text: str,
    intent: Intent,
    action_response: str,
    success: bool,
) -> tuple[str | None, uuid.UUID | None]:
    model = get_response_model(settings)
    response_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    user_prompt = json.dumps(
        {
            "user_message": message_text,
            "intent": intent.value,
            "success": success,
            "action_response": action_response,
        },
        indent=2,
    )

    try:
        text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
            settings=response_settings,
            messages=[
                {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
    except OpenAIError as exc:
        logger.exception("response_generation_failed", extra={"error": str(exc)})
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="response_generation",
            model=model,
            error=str(exc),
        )
        db.commit()
        return None, llm_call_id

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", model)

    llm_call_id = record_llm_call(
        db=db,
        session_id=session_id,
        chat_id=chat_id,
        purpose="response_generation",
        model=model_used if isinstance(model_used, str) else model,
        openrouter_generation_id=generation_id,
        usage=usage,
        latency_ms=latency_ms,
    )
    db.commit()

    return text, llm_call_id


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
) -> ProcessResult:
    """
    Process user message instantly and return response metadata.

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

    # Load user context
    context = load_context(db, user)
    classifier_context = get_context_for_classifier(context, db=db)

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

        rendered_text, response_llm_call_id = _render_response_with_llm(
            db=db,
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
            message_text=message_text,
            intent=upload_intent,
            action_response=upload_response,
            success=True,
        )
        final_text = rendered_text or upload_response

        user.last_interaction_at = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_id,
                at=dt.datetime.now(dt.UTC),
                event="file_upload_processed_v1",
                payload_json=json.dumps(
                    {
                        "intent": upload_intent.value,
                        "success": True,
                        "response_llm_call_id": str(response_llm_call_id)
                        if response_llm_call_id
                        else None,
                    }
                ),
                error=None,
            )
        )
        db.commit()
        return ProcessResult(
            response_text=final_text,
            response_llm_call_id=response_llm_call_id,
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

    decision_candidates = _build_decision_candidates(db, user, context)
    decision_result, decision_llm_call_id = _resolve_intent_with_llm(
        db=db,
        settings=settings,
        session_id=session_id,
        chat_id=chat_id,
        message_text=message_text,
        classified=classified,
        context=classifier_context,
        history=history,
        candidates=decision_candidates,
    )

    final_classified = classified
    if decision_result and decision_result.intent != Intent.UNKNOWN:
        final_classified = ClassifiedIntent(
            intent=decision_result.intent,
            params=decision_result.params,
            confidence=decision_result.confidence,
            model=decision_result.model,
            latency_ms=decision_result.latency_ms,
            generation_id=decision_result.generation_id,
            usage=decision_result.usage or {},
        )

    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="intent_resolved_v1",
            payload_json=json.dumps(
                {
                    "classifier_intent": classified.intent.value,
                    "classifier_confidence": classified.confidence,
                    "decision_intent": final_classified.intent.value,
                    "decision_confidence": final_classified.confidence,
                    "override": final_classified.intent.value != classified.intent.value,
                    "reason": decision_result.reason if decision_result else None,
                    "decision_llm_call_id": str(decision_llm_call_id)
                    if decision_llm_call_id
                    else None,
                }
            ),
            error=None,
        )
    )
    db.commit()

    # Execute the intent
    result = execute_intent(
        classified=final_classified,
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

    rendered_text, response_llm_call_id = _render_response_with_llm(
        db=db,
        settings=settings,
        session_id=session_id,
        chat_id=chat_id,
        message_text=message_text,
        intent=final_classified.intent,
        action_response=result.response,
        success=result.success,
    )
    final_text = rendered_text or result.response

    # Update user's last interaction time
    user.last_interaction_at = dt.datetime.now(dt.UTC)
    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="response_generated_v1",
            payload_json=json.dumps(
                {
                    "intent": final_classified.intent.value,
                    "success": result.success,
                    "response_llm_call_id": str(response_llm_call_id)
                    if response_llm_call_id
                    else None,
                }
            ),
            error=None,
        )
    )
    db.commit()

    # Log processing event
    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="instant_processed_v1",
            payload_json=json.dumps(
                {
                    "classifier_intent": classified.intent.value,
                    "classifier_confidence": classified.confidence,
                    "final_intent": final_classified.intent.value,
                    "final_confidence": final_classified.confidence,
                    "success": result.success,
                }
            ),
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

        return responses.outlet_select_prompt(restaurants), intent_guess

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
    # Optional telemetry params
    db: Session | None = None,
    session_id: uuid.UUID | None = None,
) -> int | None:
    """
    Send instant acknowledgment message.

    If db and session_id are provided, records the ACK in telegram_outgoing_messages.

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
