"""
Test API endpoint for synchronous message processing without Telegram.

Purpose:
- Test the full deterministic flow synchronously (no Celery)
- Get immediate response with full decision trace
- Optional console mode to print output
- Use real test/dev database

Security:
- Protected by same side-channel secret token
- Do not expose publicly
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.deterministic.execution import execute_deterministic_tool
from app.ai.deterministic.planner import plan_next_action
from app.ai.deterministic.presenter import present_tool_result
from app.ai.deterministic.schemas import PlannerCallTool, PlannerClarify
from app.ai.deterministic.tool_catalog import get_planner_tool_catalog
from app.ai.deterministic.validation import validate_planner_decision
from app.api.deps import get_db_dep, get_settings_dep
from app.conversation.context import load_context
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages
from app.db.models.telegram_outgoing_messages import TelegramOutgoingMessages
from app.db.models.user import User
from app.telegram.bot_api import send_message

router = APIRouter()
logger = logging.getLogger(__name__)


class TestMessageRequest(BaseModel):
    """Request to test a message synchronously."""

    telegram_id: int = Field(
        ...,
        description="Telegram user ID (maps to users.telegram_id).",
    )
    message: str = Field(..., description="User message text to process.")
    console_mode: bool = Field(
        default=False,
        description="If true, print bot responses to console instead of storing/sending to Telegram.",
    )
    restaurant_id: str | None = Field(
        default=None,
        description="Optional restaurant UUID to set as active context.",
    )


class TestMessageResponse(BaseModel):
    """Response from test message processing."""

    user_id: str
    telegram_id: int
    chat_id: int

    # Ack stage (future - Phase 4)
    ack_sent: bool = False
    ack_text: str | None = None

    # Planner stage
    planner_decision: dict[str, Any]
    planner_model: str | None = None
    planner_latency_ms: int | None = None
    planner_tokens: dict[str, Any] | None = None

    # Validation stage
    validation_errors: list[str]
    validation_passed: bool

    # Execution stage
    tool_executed: bool
    tool_result: dict[str, Any] | None = None
    tool_error: str | None = None

    # Presenter stage
    presenter_model: str | None = None
    presenter_latency_ms: int | None = None
    presenter_tokens: dict[str, Any] | None = None
    response_text: str

    # Console output
    console_output: list[str] | None = None


def _require_test_secret(
    *,
    settings: Settings,
    x_side_channel_secret_token: str | None,
) -> None:
    """Require side-channel secret token (reuse same secret for test endpoint)."""
    if not settings.side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Test endpoint is not configured (SIDE_CHANNEL_SECRET_TOKEN missing)",
        )
    if not x_side_channel_secret_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing side-channel secret token",
        )
    if not secrets.compare_digest(
        x_side_channel_secret_token, settings.side_channel_secret_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid side-channel secret token",
        )


def _load_recent_turns(*, db: Session, chat_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Load recent conversation turns for context."""
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


def _serialize_tool_catalog(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert tool catalog to JSON-serializable format."""
    result = []
    for tool in tools:
        if hasattr(tool, "name"):
            result.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "is_write": getattr(tool, "is_write", False),
            })
        elif isinstance(tool, dict):
            result.append(tool)
    return result


@router.post("/test/message", response_model=TestMessageResponse)
def test_message(
    payload: TestMessageRequest,
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
    x_side_channel_secret_token: str | None = Header(
        default=None, alias="X-Side-Channel-Secret-Token"
    ),
) -> TestMessageResponse:
    """
    Test message processing synchronously without Telegram.

    This endpoint processes messages through the full deterministic pipeline
    and returns the complete decision trace. Useful for testing and debugging.

    Example:
    ```bash
    curl -X POST http://localhost:8000/api/v1/test/message \\
      -H "Content-Type: application/json" \\
      -H "X-Side-Channel-Secret-Token: your-secret" \\
      -d '{
        "telegram_id": 123456789,
        "message": "show my profile",
        "console_mode": true
      }'
    ```
    """
    _require_test_secret(
        settings=settings,
        x_side_channel_secret_token=x_side_channel_secret_token,
    )

    # Load user
    user = db.scalar(select(User).where(User.telegram_id == payload.telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id={payload.telegram_id} not found. Register via Telegram first.",
        )

    # Set up chat_id (0 for console mode, real chat_id for normal mode)
    chat_id = 0 if payload.console_mode else user.chat_id

    # Track console output if in console mode
    console_output: list[str] = [] if payload.console_mode else []

    # Load context (returns UserContext object, convert to dict)
    user_context = load_context(db=db, user=user)
    context = user_context.to_dict()

    # Override active restaurant if provided
    if payload.restaurant_id:
        try:
            rid = uuid.UUID(payload.restaurant_id)
            context["active_restaurant_id"] = str(rid)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid restaurant_id format: {payload.restaurant_id}",
            )

    # Load recent conversation turns
    recent_turns = _load_recent_turns(db=db, chat_id=user.chat_id, limit=20)

    # Get tool catalog
    tool_catalog = get_planner_tool_catalog()
    tool_catalog_json = _serialize_tool_catalog(tool_catalog)

    # Stage 1: Planner
    logger.info(
        "test_api_planner_start",
        extra={
            "user_id": str(user.id),
            "telegram_id": user.telegram_id,
            "user_message": payload.message,
            "console_mode": payload.console_mode,
        },
    )

    decision, planner_telemetry = plan_next_action(
        settings=settings,
        message_text=payload.message,
        recent_turns=recent_turns,
        context=context,
        available_tools=tool_catalog_json,
    )

    planner_decision_dict = decision.model_dump() if hasattr(decision, "model_dump") else {}
    planner_model = planner_telemetry.model if planner_telemetry else None
    planner_latency_ms = planner_telemetry.latency_ms if planner_telemetry else None
    planner_tokens = planner_telemetry.usage if planner_telemetry else None

    # Stage 2: Validation
    validation = validate_planner_decision(
        decision=decision,
        tool_catalog=tool_catalog_json,
        no_ids=True,
    )

    validation_errors = validation.errors
    validation_passed = len(validation_errors) == 0
    validated_decision = validation.decision

    # Stage 3: Execution
    tool_executed = False
    tool_result = None
    tool_error = None
    response_text = ""
    presenter_model = None
    presenter_latency_ms = None
    presenter_tokens = None

    if isinstance(validated_decision, PlannerClarify):
        # Clarification needed
        response_text = validated_decision.question
        if validated_decision.choices:
            choices_text = "\n".join(
                f"{c['label']}. {c['text']}" for c in validated_decision.choices
            )
            response_text = f"{response_text}\n\n{choices_text}"

    elif isinstance(validated_decision, PlannerCallTool):
        # Execute tool
        tool_executed = True
        try:
            execution_result = execute_deterministic_tool(
                db=db,
                user=user,
                settings=settings,
                tool=validated_decision.tool,
                args=validated_decision.args,
                context=context,
            )
            tool_result = execution_result

            # Stage 4: Presenter
            presentation, presenter_telemetry = present_tool_result(
                settings=settings,
                user_message=payload.message,
                tool_result=execution_result,
            )

            response_text = presentation.text
            presenter_model = presenter_telemetry.model if presenter_telemetry else None
            presenter_latency_ms = presenter_telemetry.latency_ms if presenter_telemetry else None
            presenter_tokens = presenter_telemetry.usage if presenter_telemetry else None

        except Exception as exc:
            tool_error = str(exc)
            response_text = f"Error executing tool: {tool_error}"
            logger.exception(
                "test_api_tool_execution_failed",
                extra={
                    "user_id": str(user.id),
                    "tool": validated_decision.tool,
                    "error": tool_error,
                },
            )

    # Send response (console or silent)
    if payload.console_mode:
        console_output.append(f"[BOT] {response_text}")
        print(f"\n{'='*60}", flush=True)
        print(f"[TEST MODE] User: {payload.message}", flush=True)
        print(f"[TEST MODE] Bot: {response_text}", flush=True)
        print(f"{'='*60}\n", flush=True)
    else:
        # In non-console mode, we could optionally send to Telegram
        # but for test API, we skip it to avoid noise
        pass

    return TestMessageResponse(
        user_id=str(user.id),
        telegram_id=user.telegram_id,
        chat_id=chat_id,
        planner_decision=planner_decision_dict,
        planner_model=planner_model,
        planner_latency_ms=planner_latency_ms,
        planner_tokens=planner_tokens,
        validation_errors=validation_errors,
        validation_passed=validation_passed,
        tool_executed=tool_executed,
        tool_result=tool_result,
        tool_error=tool_error,
        presenter_model=presenter_model,
        presenter_latency_ms=presenter_latency_ms,
        presenter_tokens=presenter_tokens,
        response_text=response_text,
        console_output=console_output if payload.console_mode else None,
    )
