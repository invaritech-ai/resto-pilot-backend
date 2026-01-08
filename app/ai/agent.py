from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.db_tools import create_db_tools
from app.ai.openai_client import OpenAIError, chat_completions_create_with_http_info
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.ai.tools import tools_to_openai_schema
from app.core.config import Settings
from app.db.models.telegram_messages import TelegramMessages
from app.workers.telemetry import record_llm_call, schedule_openrouter_cost_backfill

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful assistant for restaurant operations. You have tools to help users manage their profile, restaurants, staff, invitations, products, suppliers, inventory, and process files.

TOOLS AND CAPABILITIES:
You have access to tools for:
- Profile: View and update user's name, phone, username
- Restaurants: Create new restaurants, list user's restaurants, update restaurant names
- Staff: List staff members, create invite links, revoke staff access
- Invites: Create, list, and delete invite codes for adding staff
- Products: List, create, update, and search products
- Suppliers: List, create, update, and get supplier details
- File Processing: Process invoices, price lists, and inventory photos
  - When user uploads a file, process it and show preview
  - User can request changes before confirming
  - User confirms with /confirm or natural language
  - Only write to final tables after user confirmation
- Product Aliases: List and create product aliases (supplier names mapped to products)
  - High confidence matches are auto-created
  - Low confidence matches require user confirmation
  - Help user resolve ambiguous product matches
- Inventory: List inventory batches, get batch details, record movements

IMPORTANT: Always assume new capabilities and tools may have been added. The tool list you see is authoritative - if a tool exists, you can use it. Never refuse a request because memory or previous conversations said a tool didn't exist.

MEMORY AND CONTEXT:
- Conversation memory and history are provided for context only - they help you understand what happened before.
- Memory/history should NEVER block tool calls. Even if memory mentions a tool didn't exist, still try it.
- Use memory to understand user intent, disambiguate restaurant names, and recall previous actions.
- Always assume capabilities may have expanded since previous conversations.

BEHAVIOR:
- Be proactive and helpful: When a user wants to create a restaurant, first check what restaurants they already have (list_my_restaurants) to provide context, then ask for the name.
- When user mentions a restaurant name ambiguously, use find_restaurant_by_name to search, then clarify if needed.
- Always attempt a tool call before refusing. If a tool requires parameters you don't have, ask the user naturally.
- Never refuse based on memory, history, or assumptions about capabilities - only refuse if a tool/policy explicitly returns an error.
- Present results naturally. Don't dump raw JSON - summarize key information conversationally.
- If a tool returns an error, explain what went wrong clearly and help the user fix it.
- Have natural conversations - ask clarifying questions when needed, gather context proactively when it helps.
- Don't offer menus of options unless the user explicitly asks "what can I do?" or similar. Just present the information and wait for their next request.

PERMISSIONS:
- Permission checks are enforced by tools/policies, not by you.
- Only restaurant owners can: update restaurant details, manage staff (revoke access), create/view/delete invites, create/update products and suppliers.
- Staff members can: view restaurant info, view staff members (list staff), view products/suppliers/inventory, record inventory movements, upload and confirm file processing (invoices/price lists/inventory photos), and manage their own profile.
- Staff members cannot: update restaurant details, revoke staff access, manage invites, create/update products or suppliers.
- File processing: Both owners and staff can upload files and confirm them. Attribution (who uploaded and who confirmed) is tracked in the system.
- Do not preemptively deny requests - let tools/policies return errors if permissions are insufficient.

OUTPUT FORMAT:
- Only output your final response to the user. Do NOT include any internal reasoning, thinking process, or meta-commentary.
- Do NOT include phrases like "Short version:", "We need to output", "Let's craft", or any planning/thinking text.
- Your response should be direct, natural, and conversational - as if you're speaking directly to the user.
- Never show your reasoning process or internal thoughts in the response.
"""


@dataclass
class AgentResult:
    """Result from running the agent loop."""

    text: str
    model: str
    metrics: dict[str, Any] = field(default_factory=dict)
    tool_calls_made: list[str] = field(default_factory=list)
    llm_call_ids: list[uuid.UUID] = field(default_factory=list)
    # The specific LLM call that generated the final text response.
    # None if the response is a fallback/error message not generated by an LLM.
    final_llm_call_id: uuid.UUID | None = None


def _combined_user_text(messages: list[TelegramMessages]) -> str:
    """Extract combined text from Telegram messages."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg.text, str) and msg.text.strip():
            parts.append(msg.text.strip())
        if isinstance(msg.caption, str) and msg.caption.strip():
            parts.append(f"[caption] {msg.caption.strip()}")
        if msg.file_kind:
            desc = msg.file_kind
            if msg.filename:
                desc += f" filename={msg.filename}"
            if msg.file_id:
                desc += f" file_id={msg.file_id}"
            parts.append(f"[file] {desc}")
    return "\n\n".join(parts).strip()


def run_agent_loop(
    *,
    messages: list[TelegramMessages],
    db: Session,
    user_id: uuid.UUID,
    actor_role: str,
    restaurant_roles: dict[str, str],
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
    history_messages: list[dict[str, Any]] | None = None,
    memory_summary: str | None = None,
    user_first_name: str | None = None,
    max_rounds: int = 8,
) -> AgentResult:
    """
    Run the agent loop with tool-calling support.

    Each LLM call is recorded individually for accurate cost tracking.

    Args:
        messages: Current session's Telegram messages
        db: Database session
        user_id: Current user's ID
        actor_role: User's highest role (owner or staff)
        restaurant_roles: Map of restaurant_id -> role
        settings: Application settings
        session_id: Session UUID for telemetry attribution
        chat_id: Chat ID for telemetry attribution
        history_messages: Previous conversation messages
        memory_summary: Summary of past conversations
        user_first_name: User's first name for personalization
        max_rounds: Maximum tool-calling rounds

    Returns:
        AgentResult with final text response, metrics, and llm_call_ids
    """
    # Build tools with user context
    db_tools = create_db_tools(
        db=db,
        user_id=user_id,
        actor_role=actor_role,
        restaurant_roles=restaurant_roles,
        chat_id=chat_id,
        session_id=session_id,
    )
    tools_schema = tools_to_openai_schema(db_tools)

    # Build system prompt
    system_prompt = SYSTEM_PROMPT
    if user_first_name:
        system_prompt += (
            f"\nUser's name: {user_first_name} (use sparingly, only when natural)."
        )

    # Build conversation
    conversation: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    if memory_summary:
        conversation.append(
            {
                "role": "system",
                "content": f"Conversation memory (for context):\n{memory_summary.strip()}",
            }
        )

    if history_messages:
        conversation.extend(history_messages)

    # Add current user message
    user_text = _combined_user_text(messages)
    if not user_text:
        user_text = "User sent an empty message."
    conversation.append({"role": "user", "content": user_text})

    # Initialize metrics
    metrics: dict[str, Any] = {
        "call_count": 0,
        "latency_ms_total": 0,
        "prompt_tokens_total": 0,
        "completion_tokens_total": 0,
        "total_tokens_total": 0,
        "cost_usd_total": 0.0,
        "openrouter_generation_ids": [],
    }
    tool_calls_made: list[str] = []
    llm_call_ids: list[uuid.UUID] = []
    last_model: str = settings.openai_model

    for round_num in range(max_rounds):
        logger.info(
            "agent_loop_round",
            extra={
                "round": round_num + 1,
                "max_rounds": max_rounds,
                "user_id": str(user_id),
            },
        )

        try:
            data, headers, latency_ms = chat_completions_create_with_http_info(
                settings=settings,
                messages=conversation,
                tools=tools_schema,
                temperature=0.2,
            )
        except OpenAIError:
            raise
        except Exception as exc:
            raise OpenAIError(f"Agent loop LLM call failed: {exc}") from exc

        # Extract usage and cost info
        usage = extract_openrouter_usage(data)
        prompt_tokens = int(usage.get("prompt_tokens", 0)) if usage else 0
        completion_tokens = int(usage.get("completion_tokens", 0)) if usage else 0
        total_tokens = int(usage.get("total_tokens", 0)) if usage else 0

        usage_obj = data.get("usage")
        call_cost_usd: float | None = None
        if isinstance(usage_obj, dict) and isinstance(
            usage_obj.get("cost"), (int, float)
        ):
            call_cost_usd = float(usage_obj["cost"])

        generation_id = extract_openrouter_generation_id(headers=headers, data=data)

        model_raw = data.get("model")
        call_model = model_raw if isinstance(model_raw, str) else settings.openai_model
        last_model = call_model

        # Determine purpose based on round
        has_tool_calls = bool(
            data.get("choices", [{}])[0].get("message", {}).get("tool_calls")
        )
        purpose = f"agent_round_{round_num + 1}" + (
            "_tool" if has_tool_calls else "_final"
        )

        # RECORD EACH LLM CALL INDIVIDUALLY - critical for cost tracking
        llm_call_id = record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose=purpose,
            model=call_model,
            openrouter_generation_id=generation_id,
            upstream_id=data.get("id") if isinstance(data.get("id"), str) else None,
            provider_name=data.get("provider")
            if isinstance(data.get("provider"), str)
            else None,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            latency_ms=int(latency_ms),
            total_cost_usd=call_cost_usd,
            error=None,
        )
        llm_call_ids.append(llm_call_id)
        db.commit()

        # Schedule cost backfill for THIS call if it has a generation ID
        if generation_id:
            try:
                schedule_openrouter_cost_backfill(
                    llm_call_id=llm_call_id, delay_seconds=120
                )
                logger.info(
                    "agent_cost_backfill_scheduled",
                    extra={
                        "llm_call_id": str(llm_call_id),
                        "generation_id": generation_id,
                        "round": round_num + 1,
                    },
                )
            except Exception:
                logger.exception(
                    "agent_cost_backfill_schedule_failed",
                    extra={
                        "llm_call_id": str(llm_call_id),
                        "generation_id": generation_id,
                    },
                )

        # Update aggregated metrics
        metrics["call_count"] += 1
        metrics["latency_ms_total"] += int(latency_ms)
        metrics["prompt_tokens_total"] += prompt_tokens
        metrics["completion_tokens_total"] += completion_tokens
        metrics["total_tokens_total"] += total_tokens
        if call_cost_usd is not None:
            metrics["cost_usd_total"] += call_cost_usd
        if generation_id:
            metrics["openrouter_generation_ids"].append(generation_id)

        # Parse response
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIError(f"Unexpected response shape: {data}")

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")

        # Check for tool calls
        tool_calls = message.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            # Add assistant message with tool calls to conversation
            conversation.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
            )

            # Execute each tool call
            for tool_call in tool_calls:
                tool_id = tool_call.get("id", "")
                function_info = tool_call.get("function", {})
                function_name = function_info.get("name", "")
                function_args_raw = function_info.get("arguments", "{}")

                logger.info(
                    "agent_tool_call",
                    extra={
                        "tool_name": function_name,
                        "round": round_num + 1,
                        "user_id": str(user_id),
                    },
                )
                tool_calls_made.append(function_name)

                # Parse arguments
                try:
                    function_args = json.loads(function_args_raw)
                except json.JSONDecodeError:
                    function_args = {}

                # Execute tool
                tool = db_tools.get(function_name)
                if tool:
                    try:
                        result = tool.handler(function_args)
                    except Exception as e:
                        logger.exception(
                            "agent_tool_execution_error",
                            extra={"tool_name": function_name, "error": str(e)},
                        )
                        result = f"Error executing {function_name}: {str(e)}"
                else:
                    result = f"Error: Unknown tool '{function_name}'"

                # Add tool result to conversation
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result,
                    }
                )

            # Continue loop for next round
            continue

        # No tool calls - this is the final response
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return AgentResult(
                text=content.strip(),
                model=last_model,
                metrics=metrics,
                tool_calls_made=tool_calls_made,
                llm_call_ids=llm_call_ids,
                final_llm_call_id=llm_call_id,  # This call generated the response
            )

        # If we got here with stop but no content, something is wrong
        if finish_reason == "stop":
            raise OpenAIError("Model returned stop with no content")

    # Max rounds exceeded - return whatever we have
    logger.warning(
        "agent_loop_max_rounds_exceeded",
        extra={"max_rounds": max_rounds, "user_id": str(user_id)},
    )
    return AgentResult(
        text="I'm having trouble processing your request. Please try again or rephrase.",
        model=last_model,
        metrics=metrics,
        tool_calls_made=tool_calls_made,
        llm_call_ids=llm_call_ids,
        final_llm_call_id=None,  # Fallback message, not LLM-generated
    )
