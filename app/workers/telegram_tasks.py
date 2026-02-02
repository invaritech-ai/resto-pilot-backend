from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy import select

from app.ai.deterministic.planner import plan_next_action
from app.ai.deterministic.tool_catalog import tool_catalog_as_planner_json
from app.ai.deterministic.validation import validate_planner_decision
from app.core.config import get_settings
from app.db.models.processing_events import ProcessingEvents
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.telegram_session import TelegramSessions
from app.db.models.user import User
from app.telegram.handler import handle_update_v2
from app.workers.telemetry import record_llm_call
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id

logger = logging.getLogger(__name__)


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict) -> None:
    """
    Background task to handle Telegram updates with instant intent-driven processing.

    This task uses the new handler_v2 which:
    - Processes messages instantly (no batching)
    - Uses intent classification instead of agent loop
    - Provides predictable, template-based responses

    Args:
        update: The Telegram update dictionary from the webhook
    """
    task_id = _get_task_id()
    update_id = update.get("update_id") if isinstance(update, dict) else None
    message = (
        (update.get("message") or update.get("edited_message") or {})
        if isinstance(update, dict)
        else {}
    )
    chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None

    logger.info(
        "celery_task_started name=handle_telegram_update task_id=%s update_id=%s chat_id=%s",
        task_id,
        update_id,
        chat_id,
    )
    with worker_db_session() as db:
        settings = get_settings()
        try:
            handle_update_v2(update=update, db=db, settings=settings)
        except Exception as exc:
            db.rollback()
            logger.exception(
                "telegram_update_processing_failed",
                extra={
                    "error": repr(exc),
                    "update_id": update.get("update_id"),
                    "message_id": (update.get("message") or {}).get("message_id"),
                    "chat_id": ((update.get("message") or {}).get("chat") or {}).get("id"),
                },
            )
            raise

    logger.info(
        "handle_telegram_update_completed task_id=%s update_id=%s chat_id=%s",
        task_id,
        update_id,
        chat_id,
    )


def _load_recent_turns(*, db, chat_id: int, limit: int) -> list[dict[str, str]]:
    if limit <= 0:
        return []
    incoming = db.scalars(
        select(TelegramMessages)
        .where(TelegramMessages.chat_id == chat_id)
        .order_by(TelegramMessages.received_at.desc())
        .limit(limit)
    ).all()
    outgoing = db.scalars(
        select(TelegramOutgoingMessages)
        .where(
            TelegramOutgoingMessages.chat_id == chat_id,
            TelegramOutgoingMessages.kind.in_(("reply", "start_reply")),
        )
        .order_by(TelegramOutgoingMessages.sent_at.desc())
        .limit(limit)
    ).all()

    items: list[tuple[dt.datetime, str, str]] = []
    for msg in incoming:
        parts: list[str] = []
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(msg.caption.strip())
        content = "\n".join(parts).strip()
        if content:
            items.append((msg.received_at, "user", content))
    for msg in outgoing:
        content = msg.text.strip() if isinstance(msg.text, str) else ""
        if content:
            items.append((msg.sent_at, "assistant", content))

    items.sort(key=lambda row: row[0])
    return [{"role": role, "content": content} for _, role, content in items][-limit:]


@celery_app.task(name="sidechannel_planner_only")
def sidechannel_planner_only(
    *,
    telegram_id: int,
    chat_id: int,
    message_text: str,
    recent_turns_limit: int = 20,
    context_override: dict | None = None,
) -> None:
    """
    Run PlannerLLM on the worker (no tool execution) and record telemetry.

    Used by the side-channel API to get production-like behavior (API enqueues; worker runs LLM).
    If chat_id == 0, the decision is printed to worker stdout (console-log mode).
    """
    task_id = _get_task_id()
    logger.info(
        "celery_task_started name=sidechannel_planner_only task_id=%s telegram_id=%s chat_id=%s",
        task_id,
        telegram_id,
        chat_id,
    )

    with worker_db_session() as db:
        settings = get_settings()
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            logger.warning(
                "sidechannel_planner_only_user_missing telegram_id=%s chat_id=%s",
                telegram_id,
                chat_id,
            )
            return

        # Create a session row so we can link telemetry consistently.
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

        context = user.state_data if isinstance(user.state_data, dict) else {}
        if isinstance(context_override, dict):
            merged = dict(context)
            merged.update(context_override)
            context = merged

        recent_turns = _load_recent_turns(db=db, chat_id=chat_id, limit=int(recent_turns_limit or 0))

        decision, telemetry = plan_next_action(
            settings=settings,
            message_text=message_text,
            recent_turns=recent_turns,
            context=context,
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
                session_id=session.id,
                chat_id=chat_id,
                purpose="planner_sidechannel",
                model=telemetry.model,
                openrouter_generation_id=telemetry.generation_id,
                usage=telemetry.usage,
                latency_ms=telemetry.latency_ms,
            )
            db.add(
                ProcessingEvents(
                    session_id=session.id,
                    at=dt.datetime.now(dt.UTC),
                    event="planner_sidechannel_decision_v1",
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

        db.commit()

        if chat_id == 0:
            print(json.dumps(validation.decision.model_dump(), ensure_ascii=False, indent=2), flush=True)  # noqa: T201

    logger.info(
        "sidechannel_planner_only_completed task_id=%s telegram_id=%s chat_id=%s",
        task_id,
        telegram_id,
        chat_id,
    )
