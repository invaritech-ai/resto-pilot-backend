"""
Instant message processor for the intent-driven bot.

Simplified flow:
1. Load user context
2. Resolve intent + call tools via a single LLM loop
3. Format final response with LLM
4. Update context
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent
from app.ai.model_config import get_ack_model, get_presenter_model
from app.ai.openai_client import (
    OpenAIError,
    create_chat_completion_text_allow_empty_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.tool_resolver import resolve_with_tools
from app.conversation import responses
from app.conversation.context import load_context, update_context_from_result, UserContext
from app.conversation.item_search_router import try_handle_item_search_fast_path
from app.core.config import Settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.domain.services.restaurant_service import RestaurantService
from app.telegram.bot_api import send_message
from app.workers.telemetry import record_llm_call, record_outgoing_message

logger = logging.getLogger(__name__)

_ITEM_SEARCH_ACK_SKIP_RE = re.compile(
    r"^\s*(search|find|lookup|look\s+up|show\s+me)\b|^\s*order\b",
    flags=re.IGNORECASE,
)

_FOLLOWUP_RE = re.compile(
    r"^\s*(?:how\s+about|what\s+about|howbout|how\s+abt|what\s+abt|any)\s+(?P<q>.+?)\s*[\?\!\.]*\s*$",
    flags=re.IGNORECASE,
)
_SHOW_AGAIN_RE = re.compile(
    r"\b(show|print|view|see)\b.*\b(again|preview|invoice|price\s*list|inventory|line\s*items|items)\b",
    flags=re.IGNORECASE,
)


def _extract_followup_query(message_text: str) -> str | None:
    m = _FOLLOWUP_RE.match(message_text or "")
    if not m:
        return None
    q = (m.group("q") or "").strip()
    q = q.strip(" \t\r\n?.!,")
    return q if q else None


def _is_show_again_request(message_text: str) -> bool:
    text = (message_text or "").strip().lower()
    if not text:
        return False
    if text in {"show", "show again", "print", "print again", "show preview", "print preview"}:
        return True
    return _SHOW_AGAIN_RE.search(text) is not None


def _last_list_is_fresh(settings: Settings, last_list: dict[str, Any] | None) -> bool:
    if not isinstance(last_list, dict):
        return False
    created_at = last_list.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        return False
    try:
        ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return False
    ttl_min = int(getattr(settings, "item_search_session_ttl_minutes", 30) or 30)
    return (dt.datetime.now(dt.UTC) - ts) <= dt.timedelta(minutes=ttl_min)

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


def _detect_file_type_from_text(message_text: str | None) -> str | None:
    text = (message_text or "").strip()
    if text in ("1", "2"):
        return "price_list" if text == "1" else "invoice"
    text_lower = text.lower()
    if any(
        kw in text_lower
        for kw in ["price list", "pricelist", "prices", "rate card", "catalog"]
    ):
        return "price_list"
    if any(kw in text_lower for kw in ["invoice", "bill", "receipt", "challan"]):
        return "invoice"
    return None


def _is_derive_from_file(message_text: str) -> bool:
    text = message_text.strip().lower()
    return text in {
        "derive from file",
        "use the file",
        "use file",
        "from file",
        "pull from file",
        "extract from file",
    }


def _match_restaurant_from_message(
    message_text: str | None,
    restaurants: list[dict[str, Any]],
) -> str | None:
    if not message_text:
        return None
    text = message_text.strip()
    if not text:
        return None
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(restaurants):
            return restaurants[idx - 1]["id"]

    text_lower = text.lower()
    matches: list[str] = []
    for restaurant in restaurants:
        name = restaurant.get("name", "")
        name_lower = name.lower()
        if name_lower in text_lower or text_lower in name_lower:
            matches.append(restaurant["id"])
    if len(matches) == 1:
        return matches[0]
    return None


def _decode_unicode_escapes(text: str) -> str:
    if "\\u" not in text:
        return text
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    except Exception:
        return text


def _format_tool_response_with_llm(
    *,
    db: Session,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
    message_text: str,
    tool_response: str,
) -> tuple[str | None, uuid.UUID | None]:
    model = get_presenter_model(settings)
    response_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    user_prompt = json.dumps(
        {
            "user_message": message_text,
            "tool_response": tool_response,
        },
        indent=2,
    )

    try:
        try:
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=response_settings,
                messages=[
                    {"role": "system", "content": FINAL_RESPONSE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                purpose="presenter",
                extra_body={"max_tokens": 400},
            )
        except TypeError as exc:
            if "unexpected keyword argument 'purpose'" not in str(exc):
                raise
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=response_settings,
                messages=[
                    {"role": "system", "content": FINAL_RESPONSE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                extra_body={"max_tokens": 400},
            )
    except OpenAIError as exc:
        logger.exception("final_response_failed", extra={"error": str(exc)})
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

    return _decode_unicode_escapes(text) if text else text, llm_call_id


def _generate_ack_text(
    *,
    settings: Settings,
    message_text: str,
    has_file: bool,
    file_kind: str | None,
    db: Session | None = None,
    session_id: uuid.UUID | None = None,
    chat_id: int | None = None,
) -> tuple[str | None, uuid.UUID | None]:
    model = get_ack_model(settings)
    ack_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )
    ack_settings = ack_settings.model_copy(
        update={
            "openai_timeout_seconds": min(ack_settings.openai_timeout_seconds, 6.0)
        }
    )

    user_prompt = json.dumps(
        {
            "message": message_text,
            "has_file": has_file,
            "file_kind": file_kind,
        },
        ensure_ascii=True,
    )

    try:
        try:
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=ack_settings,
                messages=[
                    {"role": "system", "content": ACK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                purpose="ack",
                extra_body={"max_tokens": 40},
            )
        except TypeError as exc:
            if "unexpected keyword argument 'purpose'" not in str(exc):
                raise
            text, data, headers, latency_ms = create_chat_completion_text_allow_empty_with_http_info(
                settings=ack_settings,
                messages=[
                    {"role": "system", "content": ACK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                extra_body={"max_tokens": 40},
            )
    except OpenAIError as exc:
        logger.exception("ack_generation_failed", extra={"error": str(exc)})
        if db is not None and session_id is not None:
            llm_call_id = record_llm_call(
                db=db,
                session_id=session_id,
                chat_id=chat_id,
                purpose="ack_generation",
                model=model,
                error=str(exc),
            )
            db.commit()
            return None, llm_call_id
        return None, None

    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", model)

    content = text.strip() if isinstance(text, str) else None
    if content:
        content = content.strip()

    llm_call_id = None
    if db is not None and session_id is not None:
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="ack_generation",
            model=model_used if isinstance(model_used, str) else model,
            openrouter_generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
            latency_ms=latency_ms,
            error=None if content else "ack_generation_empty",
        )
        db.commit()

    return _decode_unicode_escapes(content) if content else None, llm_call_id


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


def _enqueue_file_processing(
    *,
    file_type: str,
    restaurant_id: str,
    file_id: str,
    supplier_id: str | None,
    chat_id: int,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> str:
    from app.workers.tasks import (
        process_price_list_file_task,
        process_invoice_file_task,
    )
    from app.workers.celery_types import CeleryApplyAsync
    from typing import cast

    task_kwargs = {
        "restaurant_id": restaurant_id,
        "file_id": file_id,
        "supplier_id": supplier_id,
        "chat_id": chat_id,
        "user_id": str(user_id),
        "session_id": str(session_id),
    }

    if file_type == "price_list":
        cast(CeleryApplyAsync, process_price_list_file_task).apply_async(
            kwargs=task_kwargs,
            countdown=0.0,
        )
        return responses.FILE_DETECTED_PRICE_LIST

    cast(CeleryApplyAsync, process_invoice_file_task).apply_async(
        kwargs=task_kwargs,
        countdown=0.0,
    )
    return responses.FILE_DETECTED_INVOICE


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
    deterministic_enabled = bool(getattr(settings, "deterministic_execution", False))

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

    db.add(
        ProcessingEvents(
            session_id=session_id,
            at=dt.datetime.now(dt.UTC),
            event="pipeline_selected_v1",
            payload_json=json.dumps(
                {
                    "deterministic_execution": deterministic_enabled,
                    "has_file": bool(has_file),
                }
            ),
            error=None,
        )
    )
    db.commit()

    if not has_file and context.pending_action:
        # Deterministic follow-up scope selection for item search.
        if context.pending_action.get("type") == "followup_item_search_scope":
            pending = context.pending_action
            text = (message_text or "").strip().lower()
            if text in {"cancel", "/cancel", "stop"}:
                context = update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update={"clear_pending_action": True},
                )
                db.commit()
                return ProcessResult(response_text=responses.CANCEL_SUCCESS)

            if text.isdigit():
                choice = int(text)
                item_q = pending.get("item_query")
                scope = pending.get("scope") if isinstance(pending.get("scope"), dict) else {}
                supplier_name = scope.get("supplier_name") if isinstance(scope.get("supplier_name"), str) else None
                restaurant_name = scope.get("restaurant_name") if isinstance(scope.get("restaurant_name"), str) else None

                tool_name = "supplier_items_search"

                # 1) Search across all suppliers/outlets
                if choice == 1:
                    args = {"item_query": str(item_q)}
                # 2) Search within same supplier as last time (if known)
                elif choice == 2 and supplier_name:
                    args = {"item_query": str(item_q), "supplier_query": supplier_name}
                    if restaurant_name:
                        args["restaurant_query"] = restaurant_name
                # 2) List suppliers (so user can pick one)
                elif choice == 2:
                    args = {}
                    tool_name = "suppliers_list"
                else:
                    return ProcessResult(response_text="Reply with a number from the list (or /cancel).")

                from app.ai.deterministic.execution import execute_deterministic_tool
                tool_response = execute_deterministic_tool(
                    db=db,
                    user=user,
                    tool=tool_name,
                    args=args,
                    context=context.to_dict(),
                )

                context_update: dict[str, Any] = {"clear_pending_action": True}
                try:
                    parsed = json.loads(tool_response)
                    if isinstance(parsed, dict) and isinstance(parsed.get("context_update"), dict):
                        context_update.update(parsed["context_update"])
                        parsed = dict(parsed)
                        parsed.pop("context_update", None)
                        tool_response = json.dumps(parsed, indent=2)
                except Exception:
                    pass

                # Refresh last_list for subsequent follow-ups if we executed an item search.
                if tool_name == "supplier_items_search":
                    try:
                        parsed = json.loads(tool_response)
                        if isinstance(parsed, dict):
                            scope2: dict[str, Any] = {}
                            if restaurant_name:
                                scope2["restaurant_name"] = restaurant_name
                            if supplier_name:
                                scope2["supplier_name"] = supplier_name
                            context_update["last_list"] = {
                                "tool": "supplier_items_search",
                                "args": args,
                                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                                "scope": scope2,
                                "cursor": parsed.get("cursor") if isinstance(parsed.get("cursor"), str) else None,
                                "has_more": bool(parsed.get("has_more")) if "has_more" in parsed else None,
                            }
                    except Exception:
                        pass

                context = update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update=context_update,
                )
                db.commit()

                # Presenter: use PresenterLLM (model selectable). If it fails, deterministic fallback.
                formatted_text, response_llm_call_id = _format_tool_response_with_llm(
                    db=db,
                    settings=settings,
                    session_id=session_id,
                    chat_id=chat_id,
                    message_text=message_text,
                    tool_response=tool_response,
                )
                if formatted_text:
                    final_text = formatted_text
                else:
                    from app.ai.deterministic.presenter import present_tool_result
                    final_text = present_tool_result(tool=tool_name, tool_response_json=tool_response)

                user.last_interaction_at = dt.datetime.now(dt.UTC)
                db.add(
                    ProcessingEvents(
                        session_id=session_id,
                        at=dt.datetime.now(dt.UTC),
                        event="followup_scope_choice_v1",
                        payload_json=json.dumps(
                            {
                                "choice": choice,
                                "tool": tool_name,
                            }
                        ),
                        error=None,
                    )
                )
                db.commit()
                return ProcessResult(response_text=final_text, response_llm_call_id=response_llm_call_id)

            return ProcessResult(response_text="Reply with a number from the list (or /cancel).")

        pending_result = _handle_pending_file_processing_missing_field(
            db=db,
            user=user,
            context=context,
            message_text=message_text,
            chat_id=chat_id,
            session_id=session_id,
        )
        if pending_result is not None:
            response_text, context_update = pending_result
            if context_update:
                context = update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update=context_update,
                )
                db.commit()
            return ProcessResult(response_text=response_text)

        pending_result = _handle_pending_file_processing_confirm(
            db=db,
            user=user,
            context=context,
            message_text=message_text,
            chat_id=chat_id,
            session_id=session_id,
            settings=settings,
        )
        if pending_result is not None:
            response_text, context_update = pending_result
            if context_update:
                context = update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update=context_update,
                )
                db.commit()
            return ProcessResult(response_text=response_text)

        pending_result = _handle_pending_file_upload(
            db=db,
            user=user,
            context=context,
            message_text=message_text,
            session_id=session_id,
            chat_id=chat_id,
        )
        if pending_result is not None:
            response_text, context_update = pending_result
            if context_update:
                context = update_context_from_result(
                    db=db,
                    user=user,
                    context=context,
                    context_update=context_update,
                )
                db.commit()
            return ProcessResult(response_text=response_text)

    # Handle file uploads specially - detect type with vision
    if has_file and file_id:
        upload_response, upload_intent, context_update = _handle_file_upload(
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
        if context_update:
            context = update_context_from_result(
                db=db,
                user=user,
                context=context,
                context_update=context_update,
            )
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

    # Deterministic execution mode: Planner -> (Clarify OR call one deterministic tool) -> Presenter.
    # Runs on the worker via handle_update_v2, so the API is never blocked.
    if deterministic_enabled:
        from app.ai.deterministic.execution import execute_deterministic_tool
        from app.ai.deterministic.planner import plan_next_action
        from app.ai.deterministic.schemas import PlannerCallTool, PlannerClarify
        from app.ai.deterministic.tool_catalog import tool_catalog_as_planner_json
        from app.ai.deterministic.validation import validate_planner_decision

        # Deterministic follow-up: "how about X?" after an item search list.
        follow_q = _extract_followup_query(message_text or "")
        last_list = context.last_list if hasattr(context, "last_list") else None
        if (
            follow_q
            and isinstance(last_list, dict)
            and last_list.get("tool") == "supplier_items_search"
            and _last_list_is_fresh(settings, last_list)
        ):
            scope = last_list.get("scope") if isinstance(last_list.get("scope"), dict) else {}
            supplier_name = scope.get("supplier_name") if isinstance(scope.get("supplier_name"), str) else None
            restaurant_name = scope.get("restaurant_name") if isinstance(scope.get("restaurant_name"), str) else None

            # Unambiguous: reuse last scope, run directly (no planner needed).
            args: dict[str, Any] = {"item_query": follow_q}
            if restaurant_name:
                args["restaurant_query"] = restaurant_name
            if supplier_name:
                args["supplier_query"] = supplier_name

            tool_response = execute_deterministic_tool(
                db=db,
                user=user,
                tool="supplier_items_search",
                args=args,
                context=context.to_dict(),
            )

            try:
                parsed = json.loads(tool_response)
                if isinstance(parsed, dict) and isinstance(parsed.get("context_update"), dict):
                    context = update_context_from_result(
                        db=db,
                        user=user,
                        context=context,
                        context_update=parsed["context_update"],
                    )
                    parsed = dict(parsed)
                    parsed.pop("context_update", None)
                    tool_response = json.dumps(parsed, indent=2)
            except Exception:
                pass

            # Refresh last_list for subsequent follow-ups.
            try:
                parsed = json.loads(tool_response)
                if isinstance(parsed, dict):
                    scope2: dict[str, Any] = {}
                    if restaurant_name:
                        scope2["restaurant_name"] = restaurant_name
                    if supplier_name:
                        scope2["supplier_name"] = supplier_name
                    context = update_context_from_result(
                        db=db,
                        user=user,
                        context=context,
                        context_update={
                            "last_list": {
                                "tool": "supplier_items_search",
                                "args": args,
                                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                                "scope": scope2,
                                "cursor": parsed.get("cursor") if isinstance(parsed.get("cursor"), str) else None,
                                "has_more": bool(parsed.get("has_more")) if "has_more" in parsed else None,
                            }
                        },
                    )
                    db.commit()
            except Exception:
                pass

            formatted_text, response_llm_call_id = _format_tool_response_with_llm(
                db=db,
                settings=settings,
                session_id=session_id,
                chat_id=chat_id,
                message_text=message_text,
                tool_response=tool_response,
            )
            if formatted_text:
                final_text = formatted_text
            else:
                from app.ai.deterministic.presenter import present_tool_result
                final_text = present_tool_result(tool="supplier_items_search", tool_response_json=tool_response)

            db.add(
                ProcessingEvents(
                    session_id=session_id,
                    at=dt.datetime.now(dt.UTC),
                    event="deterministic_followup_item_search_v1",
                    payload_json=json.dumps(
                        {"item_query": follow_q, "scope": scope},
                        ensure_ascii=False,
                    ),
                    error=None,
                )
            )
            db.commit()
            return ProcessResult(response_text=final_text, response_llm_call_id=response_llm_call_id)

        tool_catalog = tool_catalog_as_planner_json()
        decision, telemetry = plan_next_action(
            settings=settings,
            message_text=message_text or "",
            recent_turns=history,
            context=context.to_dict(),
            available_tools=tool_catalog,
        )
        validation = validate_planner_decision(
            decision=decision,
            tool_catalog=tool_catalog,
            no_ids=True,
        )

        planner_llm_call_id: uuid.UUID | None = None
        if telemetry is not None:
            planner_llm_call_id = record_llm_call(
                db=db,
                session_id=session_id,
                chat_id=chat_id,
                purpose="planner",
                model=telemetry.model,
                openrouter_generation_id=telemetry.generation_id,
                usage=telemetry.usage,
                latency_ms=telemetry.latency_ms,
            )
            db.add(
                ProcessingEvents(
                    session_id=session_id,
                    at=dt.datetime.now(dt.UTC),
                    event="planner_decision_v1",
                    payload_json=json.dumps(
                        {
                            "decision": decision.model_dump(),
                            "validated_decision": validation.decision.model_dump(),
                            "validation_errors": validation.errors,
                            "llm_call_id": str(planner_llm_call_id),
                        },
                        ensure_ascii=False,
                    ),
                    error=None,
                )
            )
            db.commit()

        validated = validation.decision
        if isinstance(validated, PlannerClarify):
            lines: list[str] = []
            question = validated.question.strip() if isinstance(validated.question, str) else ""
            lines.append(question or "Please clarify what you want to do.")
            if validated.choices:
                for choice in validated.choices:
                    label = str(choice.label).strip()
                    text = str(choice.text).strip()
                    if label and text:
                        if label.endswith(")"):
                            lines.append(f"{label} {text}")
                        else:
                            lines.append(f"{label}) {text}")
            final_text = "\n".join([ln for ln in lines if ln.strip()]).strip()

            # Optional ClarifierLLM (phrasing only). If it fails, fall back to deterministic text above.
            clarifier_llm_call_id: uuid.UUID | None = None
            if bool(getattr(settings, "clarification_model", "").strip()):
                try:
                    from app.ai.deterministic.clarifier import clarify_text

                    llm_text, clarifier_telemetry = clarify_text(
                        settings=settings,
                        clarify_kind=validated.clarify_kind,
                        question=question or "Please clarify what you want to do.",
                        choices=[
                            {"label": str(c.label), "text": str(c.text)}
                            for c in (validated.choices or [])
                            if c.label and c.text
                        ]
                        if validated.choices
                        else None,
                        user_message=message_text or "",
                        recent_turns=history,
                        context=context.to_dict(),
                        available_tools=tool_catalog,
                    )
                    if clarifier_telemetry is not None:
                        clarifier_llm_call_id = record_llm_call(
                            db=db,
                            session_id=session_id,
                            chat_id=chat_id,
                            purpose="clarifier",
                            model=str(clarifier_telemetry.get("model") or ""),
                            openrouter_generation_id=clarifier_telemetry.get("generation_id"),
                            usage=clarifier_telemetry.get("usage") or {},
                            latency_ms=clarifier_telemetry.get("latency_ms"),
                        )
                        db.commit()
                    if isinstance(llm_text, str) and llm_text.strip():
                        final_text = llm_text.strip()
                except Exception:
                    logger.exception("deterministic_clarifier_failed")

            user.last_interaction_at = dt.datetime.now(dt.UTC)
            db.add(
                ProcessingEvents(
                    session_id=session_id,
                    at=dt.datetime.now(dt.UTC),
                    event="deterministic_clarify_v1",
                    payload_json=json.dumps(
                        {
                            "clarify_kind": validated.clarify_kind,
                            "llm_call_id": str(planner_llm_call_id) if planner_llm_call_id else None,
                            "clarifier_llm_call_id": str(clarifier_llm_call_id) if clarifier_llm_call_id else None,
                        },
                        ensure_ascii=False,
                    ),
                    error=None,
                )
            )
            db.commit()
            return ProcessResult(response_text=final_text)

        assert isinstance(validated, PlannerCallTool)
        tool_response = execute_deterministic_tool(
            db=db,
            user=user,
            tool=validated.tool,
            args=validated.args or {},
            context=context.to_dict(),
        )

        # Apply context updates if the tool returned one.
        context_update: dict[str, Any] | None = None
        try:
            parsed = json.loads(tool_response)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("context_update"), dict):
                    context_update = parsed["context_update"]
                # Never expose internal context updates (often contain UUIDs) to the Presenter/user.
                if "context_update" in parsed:
                    parsed = dict(parsed)
                    parsed.pop("context_update", None)
                    tool_response = json.dumps(parsed, indent=2)
        except Exception:
            context_update = None

        # Cache last list context for follow-ups ("how about X?") and numbered selection.
        if validated.tool in {"supplier_items_search", "restaurants_list", "staff_list", "suppliers_list", "invite_codes_list"}:
            try:
                parsed = json.loads(tool_response)
                if isinstance(parsed, dict):
                    scope: dict[str, Any] = {}
                    if isinstance(parsed.get("restaurant_name"), str) and parsed["restaurant_name"].strip():
                        scope["restaurant_name"] = parsed["restaurant_name"].strip()
                    supplier_name = parsed.get("supplier_name")
                    if isinstance(supplier_name, str) and supplier_name.strip():
                        scope["supplier_name"] = supplier_name.strip()
                    if validated.tool == "supplier_items_search" and "supplier_name" not in scope:
                        items = parsed.get("items")
                        if isinstance(items, list):
                            suppliers = {
                                it.get("supplier", "").strip()
                                for it in items
                                if isinstance(it, dict) and isinstance(it.get("supplier"), str) and it.get("supplier").strip()
                            }
                            if len(suppliers) == 1:
                                scope["supplier_name"] = next(iter(suppliers))

                    last_list = {
                        "tool": validated.tool,
                        "args": validated.args,
                        "created_at": dt.datetime.now(dt.UTC).isoformat(),
                        "scope": scope,
                    }
                    if "cursor" in parsed and isinstance(parsed.get("cursor"), str):
                        last_list["cursor"] = parsed.get("cursor")
                    if "has_more" in parsed:
                        last_list["has_more"] = bool(parsed.get("has_more"))
                    if context_update is None:
                        context_update = {}
                    context_update["last_list"] = last_list
            except Exception:
                pass

        if context_update:
            context = update_context_from_result(
                db=db,
                user=user,
                context=context,
                context_update=context_update,
            )

        # Presenter: use PresenterLLM (model selectable). If it fails, fall back to deterministic presenter.
        formatted_text, response_llm_call_id = _format_tool_response_with_llm(
            db=db,
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
            message_text=message_text,
            tool_response=tool_response,
        )
        if formatted_text:
            final_text = formatted_text
        else:
            from app.ai.deterministic.presenter import present_tool_result
            final_text = present_tool_result(tool=validated.tool, tool_response_json=tool_response)

        user.last_interaction_at = dt.datetime.now(dt.UTC)
        db.add(
            ProcessingEvents(
                session_id=session_id,
                at=dt.datetime.now(dt.UTC),
                event="deterministic_tool_execution_v1",
                payload_json=json.dumps(
                    {
                        "tool": validated.tool,
                        "args": validated.args,
                        "planner_llm_call_id": str(planner_llm_call_id) if planner_llm_call_id else None,
                        "response_llm_call_id": str(response_llm_call_id) if response_llm_call_id else None,
                        "validation_errors": validation.errors,
                        "has_context_update": bool(context_update),
                        "presenter": "llm_with_deterministic_fallback_v1",
                    },
                    ensure_ascii=False,
                ),
                error=None,
            )
        )
        db.commit()
        return ProcessResult(response_text=final_text, response_llm_call_id=response_llm_call_id)

    # Shadow mode: run deterministic PlannerLLM (no tool execution) and record telemetry.
    # This is used to tune prompts locally/in staging without affecting behavior.
    if bool(getattr(settings, "deterministic_shadow_mode", False)):
        try:
            from app.ai.deterministic.planner import plan_next_action
            from app.ai.deterministic.tool_catalog import tool_catalog_as_planner_json
            from app.ai.deterministic.validation import validate_planner_decision

            decision, telemetry = plan_next_action(
                settings=settings,
                message_text=message_text or "",
                recent_turns=history,
                context=context.to_dict(),
                available_tools=tool_catalog_as_planner_json(),
            )
            validation = validate_planner_decision(
                decision=decision,
                tool_catalog=tool_catalog_as_planner_json(),
                no_ids=True,
            )
            if telemetry is not None:
                llm_call_id = record_llm_call(
                    db=db,
                    session_id=session_id,
                    chat_id=chat_id,
                    purpose="planner_shadow",
                    model=telemetry.model,
                    openrouter_generation_id=telemetry.generation_id,
                    usage=telemetry.usage,
                    latency_ms=telemetry.latency_ms,
                )
                db.add(
                    ProcessingEvents(
                        session_id=session_id,
                        at=dt.datetime.now(dt.UTC),
                        event="planner_shadow_decision_v1",
                        payload_json=json.dumps(
                            {
                                "decision": decision.model_dump(),
                                "validated_decision": validation.decision.model_dump(),
                                "validation_errors": validation.errors,
                                "llm_call_id": str(llm_call_id),
                            },
                            ensure_ascii=False,
                        ),
                        error=None,
                    )
                )
        except Exception:
            logger.exception("planner_shadow_failed")

    # Item search fast-path (minimize LLM calls + deterministic formatting)
    fast = try_handle_item_search_fast_path(
        db=db,
        user_id=user.id,
        context=context,
        message_text=message_text or "",
        settings=settings,
        session_id=session_id,
        chat_id=chat_id,
    )
    if fast is not None:
        response_text, context_update = fast
        if context_update:
            context = update_context_from_result(
                db=db,
                user=user,
                context=context,
                context_update=context_update,
            )
        user.last_interaction_at = dt.datetime.now(dt.UTC)
        db.commit()
        return ProcessResult(response_text=response_text)

    tool_result = resolve_with_tools(
        db=db,
        user=user,
        message_text=message_text or "",
        history=history,
        active_restaurant_id=context.active_restaurant_id,
        active_supplier_id=context.active_supplier_id,
        pending_action=context.pending_action,
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

    formatted_text, response_llm_call_id = _format_tool_response_with_llm(
        db=db,
        settings=settings,
        session_id=session_id,
        chat_id=chat_id,
        message_text=message_text,
        tool_response=tool_result.response_text,
    )
    final_text = formatted_text or tool_result.response_text

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
                "tool_names": getattr(tool_result, "tool_names", []),
                "exit_reason": getattr(tool_result, "exit_reason", None),
            }),
            error=None,
        )
    )
    db.commit()

    return ProcessResult(
        response_text=final_text,
        response_llm_call_id=response_llm_call_id,
    )


def _handle_pending_file_upload(
    *,
    db: Session,
    user: User,
    context: UserContext,
    message_text: str,
    session_id: uuid.UUID,
    chat_id: int,
) -> tuple[str, dict[str, Any]] | None:
    pending_action = context.pending_action
    if not pending_action or pending_action.get("type") != "file_upload_pending":
        return None

    file_id = pending_action.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        return None

    file_type = pending_action.get("file_type")
    if not isinstance(file_type, str) or not file_type:
        file_type = _detect_file_type_from_text(message_text)
        if not file_type:
            return responses.FILE_DETECTION_UNSURE, {"pending_action": pending_action}

    restaurant_id = pending_action.get("restaurant_id")
    if not restaurant_id:
        restaurants = _get_user_restaurants(db, user.id)
        if not restaurants:
            return responses.ERROR_NO_OUTLETS, {"clear_pending_action": True}
        restaurant_id = _match_restaurant_from_message(message_text, restaurants)
        if not restaurant_id:
            updated_action = dict(pending_action)
            updated_action["file_type"] = file_type
            return (
                responses.outlet_select_prompt(restaurants),
                {"pending_action": updated_action},
            )

    supplier_id = pending_action.get("supplier_id") or context.active_supplier_id
    response_text = _enqueue_file_processing(
        file_type=file_type,
        restaurant_id=str(restaurant_id),
        file_id=str(file_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        chat_id=chat_id,
        user_id=user.id,
        session_id=session_id,
    )
    context_update = {
        "clear_pending_action": True,
        "active_restaurant_id": str(restaurant_id),
    }
    if supplier_id:
        context_update["active_supplier_id"] = str(supplier_id)
    return response_text, context_update


def _handle_pending_file_processing_missing_field(
    *,
    db: Session,
    user: User,
    context: UserContext,
    message_text: str,
    chat_id: int,
    session_id: uuid.UUID,
) -> tuple[str, dict[str, Any]] | None:
    pending_action = context.pending_action
    if not pending_action or pending_action.get("type") != "file_processing_missing_field":
        return None

    staging_id = pending_action.get("staging_id")
    field = pending_action.get("field")
    if not isinstance(staging_id, str) or not staging_id:
        return None
    if field not in ("supplier", "currency"):
        return None

    value = message_text.strip()
    if not value:
        prompt = (
            "Please provide the supplier name."
            if field == "supplier"
            else "Please provide the currency (e.g., USD, EUR)."
        )
        return prompt, {"pending_action": pending_action}

    if _is_derive_from_file(value):
        prompt = (
            "I couldn't find the supplier in the file. Please provide the supplier name."
            if field == "supplier"
            else "I couldn't find the currency in the file. Please provide it (e.g., USD, EUR)."
        )
        return prompt, {"pending_action": pending_action}

    from app.ai.db_tools import file_processing as file_processing_tools
    from app.db.models.file_processing_staging import FileProcessingStaging

    tools = file_processing_tools.create_file_processing_tools(
        db=db,
        user_id=user.id,
        actor_role="staff",
        restaurant_roles={},
        chat_id=chat_id,
        session_id=session_id,
    )
    tool = tools.get("update_missing_field")
    if not tool:
        return responses.CANT_HELP, {"pending_action": pending_action}

    result = tool.handler({"staging_id": staging_id, "value": value})
    if isinstance(result, str) and result.startswith("Error:"):
        return result, {"pending_action": pending_action}

    try:
        staging_uuid = uuid.UUID(staging_id)
    except ValueError:
        return result, {"pending_action": pending_action}

    staging = db.get(FileProcessingStaging, staging_uuid)
    if not staging:
        return result, {"pending_action": pending_action}

    context_update = {"active_restaurant_id": str(staging.restaurant_id)}
    if staging.status == "awaiting_currency":
        context_update["pending_action"] = {
            "type": "file_processing_missing_field",
            "staging_id": staging_id,
            "field": "currency",
        }
    elif staging.status == "awaiting_supplier":
        context_update["pending_action"] = {
            "type": "file_processing_missing_field",
            "staging_id": staging_id,
            "field": "supplier",
        }
    else:
        context_update["clear_pending_action"] = True

    return result, context_update


def _handle_pending_file_processing_confirm(
    *,
    db: Session,
    user: User,
    context: UserContext,
    message_text: str,
    chat_id: int,
    session_id: uuid.UUID,
    settings: Settings,
) -> tuple[str, dict[str, Any]] | None:
    pending_action = context.pending_action
    if not pending_action or pending_action.get("type") != "file_processing_confirm":
        return None

    staging_id = pending_action.get("staging_id")
    if not isinstance(staging_id, str) or not staging_id:
        return None

    text_lower = message_text.strip().lower().rstrip("!?.,")
    confirm_tokens = {"/confirm", "confirm", "yes", "looks good", "save", "ok"}
    if text_lower in confirm_tokens:
        from app.ai.db_tools import file_processing as file_processing_tools
        from app.db.models.file_processing_staging import FileProcessingStaging

        tools = file_processing_tools.create_file_processing_tools(
            db=db,
            user_id=user.id,
            actor_role="staff",
            restaurant_roles={},
            chat_id=chat_id,
            session_id=session_id,
        )
        tool = tools.get("confirm_file_processing")
        if not tool:
            return responses.CANT_HELP, {"pending_action": pending_action}

        result = tool.handler({"staging_id": staging_id})
        if isinstance(result, str) and result.startswith("Error:"):
            return result, {"pending_action": pending_action}

        try:
            staging_uuid = uuid.UUID(staging_id)
        except ValueError:
            return result, {"clear_pending_action": True}

        staging = db.get(FileProcessingStaging, staging_uuid)
        context_update = {"clear_pending_action": True}
        if staging:
            context_update["active_restaurant_id"] = str(staging.restaurant_id)

        return result, context_update

    from app.ai.db_tools import file_processing as file_processing_tools
    from app.db.models.file_processing_staging import FileProcessingStaging

    tools = file_processing_tools.create_file_processing_tools(
        db=db,
        user_id=user.id,
        actor_role="staff",
        restaurant_roles={},
        chat_id=chat_id,
        session_id=session_id,
    )

    if _is_show_again_request(message_text):
        review_tool = tools.get("review_file_processing")
        if not review_tool:
            return responses.CANT_HELP, {"pending_action": pending_action}
        result = review_tool.handler({"staging_id": staging_id})
        return result, {"pending_action": pending_action}

    try:
        staging_uuid = uuid.UUID(staging_id)
    except ValueError:
        return "Say /confirm to save this file.", {"pending_action": pending_action}

    staging = db.get(FileProcessingStaging, staging_uuid)
    if not staging:
        return "File processing record not found.", {"pending_action": pending_action}

    from app.ai.file_processing_edit_parser import parse_file_processing_edits

    extracted_data = staging.extracted_data_json if isinstance(staging.extracted_data_json, dict) else {}
    updates, telemetry = parse_file_processing_edits(
        settings=settings,
        user_message=message_text,
        processing_type=staging.processing_type,
        extracted_data=extracted_data,
    )
    if telemetry is not None:
        record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="file_processing_edit_parse",
            model=telemetry.model,
            openrouter_generation_id=telemetry.generation_id,
            usage=telemetry.usage,
            latency_ms=telemetry.latency_ms,
        )
        db.commit()

    if not updates:
        return (
            "Tell me what to change, or say /confirm to save.",
            {"pending_action": pending_action},
        )

    update_tool = tools.get("update_file_processing_data")
    review_tool = tools.get("review_file_processing")
    if not update_tool or not review_tool:
        return responses.CANT_HELP, {"pending_action": pending_action}

    for update in updates:
        field_path = update.get("field_path")
        if not isinstance(field_path, str) or not field_path.strip():
            continue
        result = update_tool.handler(
            {
                "staging_id": staging_id,
                "field_path": field_path.strip(),
                "new_value": update.get("new_value"),
            }
        )
        if isinstance(result, str) and result.startswith("Error:"):
            return result, {"pending_action": pending_action}

    preview = review_tool.handler({"staging_id": staging_id})
    context_update = {
        "pending_action": pending_action,
        "last_file": {"staging_id": staging_id, "processing_type": staging.processing_type},
    }
    return preview, context_update


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
) -> tuple[str, Intent, dict[str, Any]]:
    """
    Handle file upload with type detection.

    Flow:
    1. Detect file type from caption/context
    2. Queue file processing
    3. Return appropriate response
    """
    # Determine file type from caption/context
    file_type = _detect_file_type_from_text(message_text)
    intent_guess = (
        Intent.UPLOAD_PRICE_LIST if file_type == "price_list" else Intent.UPLOAD_INVOICE
    )

    # Get restaurant ID
    restaurants = _get_user_restaurants(db, user.id)
    if not restaurants:
        return responses.ERROR_NO_OUTLETS, intent_guess

    restaurant_id = None
    if len(restaurants) == 1:
        restaurant_id = restaurants[0]["id"]
    else:
        restaurant_id = _match_restaurant_from_message(message_text, restaurants)

    # Get supplier_id from context if available
    supplier_id = context.active_supplier_id
    if not file_type:
        return (
            responses.FILE_DETECTION_UNSURE,
            intent_guess,
            {
                "pending_action": {
                    "type": "file_upload_pending",
                    "file_id": file_id,
                    "file_kind": file_kind,
                    "restaurant_id": restaurant_id,
                    "supplier_id": supplier_id,
                }
            },
        )

    if not restaurant_id:
        return (
            responses.outlet_select_prompt(restaurants),
            intent_guess,
            {
                "pending_action": {
                    "type": "file_upload_pending",
                    "file_id": file_id,
                    "file_kind": file_kind,
                    "restaurant_id": restaurant_id,
                    "supplier_id": supplier_id,
                    "file_type": file_type,
                }
            },
        )

    response_text = _enqueue_file_processing(
        file_type=file_type,
        restaurant_id=str(restaurant_id),
        file_id=file_id,
        supplier_id=supplier_id,
        chat_id=chat_id,
        user_id=user.id,
        session_id=session_id,
    )
    return response_text, intent_guess, {}


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

    Uses LLM generation with a short response and falls back to static ACK messages.

    Returns the Telegram message ID or None if sending failed.
    """
    text = responses.ACK_FILE_PROCESSING if has_file else responses.ACK_PROCESSING
    llm_call_id: uuid.UUID | None = None
    # Always attempt LLM ack (best-effort). Fall back to static ACK if it fails.
    if message_text.strip() or has_file:
        ack_text, llm_call_id = _generate_ack_text(
            settings=settings,
            message_text=message_text,
            has_file=has_file,
            file_kind=file_kind,
            db=db,
            session_id=session_id,
            chat_id=chat_id,
        )
        if ack_text:
            text = ack_text
    
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
                llm_call_id=llm_call_id,
            )

        return telegram_message_id
    except Exception as e:
        logger.warning(
            "ack_send_failed",
            extra={"chat_id": chat_id, "error": str(e)},
        )
        return None

ACK_SYSTEM_PROMPT = """You are an acknowledgment generator for a restaurant management bot.
Return a single short sentence (max 10 words). No emojis, no markdown.
If has_file is true, acknowledge receipt of the file.
Do not answer the user, ask questions, or provide options. Just acknowledge receipt."""

FINAL_RESPONSE_SYSTEM_PROMPT = """You are a response composer for a restaurant management bot.
Use only the provided tool response and user message. Do not invent facts.
Keep responses short, crisp, and helpful (1-3 sentences).
If the tool response is JSON, summarize it clearly.
If the JSON includes a suppliers list (keys like "suppliers" or "unlinked_suppliers"), list only supplier names and do not mention restaurant/outlet names unless the user explicitly asked for a specific outlet.
For other JSON payloads, you may include restaurant_name in the header when it helps.
If the tool response contains a structured list or formatted block, keep it.
Use real Unicode characters; do not escape emojis or other symbols.
"""
