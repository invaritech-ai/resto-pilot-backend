"""
Intent resolver for the static bot.

Single LLM call that classifies intent AND resolves all entity names to UUIDs.
Replaces the previous two-step flow (classify_intent + _resolve_intent_with_llm).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intent_classifier import Intent
from app.ai.model_config import get_intent_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings
from app.db.models.suppliers import Suppliers
from app.db.models.user import User
from app.domain.services.restaurant_service import RestaurantService

logger = logging.getLogger(__name__)


@dataclass
class ResolvedIntent:
    """Result of intent resolution - includes fully resolved params."""

    intent: Intent
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_message: str | None = None

    # Telemetry
    model: str = ""
    latency_ms: int = 0
    generation_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


RESOLVER_SYSTEM_PROMPT = """You are an intent resolver for a restaurant management bot.

Your job is to:
1. Classify the user's message into one of the supported intents
2. Resolve ALL entity names to their UUIDs using the provided candidates
3. Return structured JSON with fully resolved parameters

## Supported Intents

### Profile Management
- view_profile: User wants to see their profile
- update_name: User wants to change their name. Params: "name"
- update_phone: User wants to change their phone. Params: "phone"

### Outlet/Restaurant Management
- list_outlets: User wants to see their outlets/restaurants
- add_outlet: User wants to create a new outlet. Params: "name"
- update_outlet: User wants to rename an outlet. Params: "restaurant_id", "name"
- list_staff: User wants to see staff members. Params: "restaurant_id"
- add_staff: User wants to invite staff. Params: "restaurant_id", "role" (staff/owner)
- revoke_staff: User wants to remove staff. Params: "restaurant_id", "user_id"

### Supplier Management
- list_suppliers: User wants to see suppliers. Params: "restaurant_id"
- add_supplier: User wants to add a supplier. Params: "restaurant_id", "name"
- update_supplier: User wants to update supplier details. Params: "supplier_id", "name"
- view_supplier: User wants to see supplier details. Params: "supplier_id"
- view_supplier_price_list: User wants to see supplier's current prices. Params: "supplier_id"
- view_supplier_items: User wants to see items a supplier offers. Params: "supplier_id"
- deactivate_supplier: User wants to delete/remove a supplier. Params: "supplier_id"

### Invites
- list_invites: User wants to see active invite links. Params: "restaurant_id"
- move_invite: User wants to move an invite to a different outlet. Params: "restaurant_id", "invite_code"

### Inventory Management
- list_inventory: User wants to see inventory. Params: "restaurant_id"
- add_inventory: User wants to record received inventory. Params: "restaurant_id"
- update_inventory: User wants to adjust inventory quantity. Params: "batch_id"
- log_inventory_usage: User wants to log usage/waste. Params: "batch_id", "quantity", "reason"
- list_locations: User wants to see storage locations. Params: "restaurant_id"
- add_location: User wants to add a storage location. Params: "restaurant_id", "name"

### Invoices
- list_invoices: User wants to see past invoices. Params: "restaurant_id"
- view_invoice: User wants to see invoice details. Params: "invoice_id"

### File Processing
- upload_price_list: User wants to upload a price list
- upload_invoice: User wants to upload an invoice
- confirm_upload: User confirms processed file data

### Navigation
- show_menu: User greets or wants options ("hi", "hello", "menu", "help")
- cancel: User wants to cancel ("cancel", "nevermind")
- help: User asks about capabilities. Params: optional "topic"

### Unknown
- unknown: Message doesn't match any supported intent

## Entity Resolution Rules

You will receive entity candidates with their UUIDs. Your job is to:

1. **Match restaurant names flexibly**: Handle typos, spacing, partial names, case variations.
   - "joyful" → "Joyful Banquet" 
   - "joy ful" → "Joyful Banquet"
   - Use the exact UUID from candidates.

2. **Match supplier names flexibly**: Same fuzzy matching rules.

3. **Match staff by name or username**: Look in the staff array.

4. **Use active context**: If active_restaurant_id is set, use it when not specified.

5. **Auto-select single option**: If user has only one restaurant, use it automatically.

## Clarification

If you cannot resolve a required entity:
- Set needs_clarification: true
- Provide a helpful clarification_message asking the user to specify

Do NOT ask for clarification if you can reasonably infer the entity.

## Output Format

Return ONLY valid JSON:
{
  "intent": "intent_name",
  "params": {"restaurant_id": "uuid-here", "name": "value"},
  "confidence": 0.95,
  "needs_clarification": false,
  "clarification_message": null
}
"""


def _format_history(
    history: list[dict[str, str]] | None,
    limit: int = 20,
) -> str | None:
    """Format conversation history for the prompt."""
    if not history:
        return None

    lines: list[str] = []
    for msg in history[-limit:]:
        role = msg.get("role", "")
        content = msg.get("content", "")[:200]
        if role and content:
            lines.append(f"{role}: {content}")
    
    return "\n".join(lines) if lines else None


def _build_candidates(
    db: Session,
    user_id: uuid.UUID,
    active_restaurant_id: str | None,
    max_suppliers: int = 50,
    max_staff: int = 50,
) -> dict[str, Any]:
    """Build entity candidates for the resolver."""
    # Get user's restaurants
    service = RestaurantService(db)
    rows = service.list_for_user(user_id=user_id)
    restaurants = [
        {"id": str(r.id), "name": r.name, "role": m.role}
        for r, m in rows
    ]
    
    candidates: dict[str, Any] = {"restaurants": restaurants}

    # Determine which restaurant to load suppliers/staff from
    restaurant_uuid: uuid.UUID | None = None
    if active_restaurant_id:
        try:
            restaurant_uuid = uuid.UUID(active_restaurant_id)
        except (ValueError, AttributeError):
            pass
    elif len(restaurants) == 1:
        try:
            restaurant_uuid = uuid.UUID(restaurants[0]["id"])
        except (ValueError, AttributeError):
            pass

    if not restaurant_uuid:
        return candidates

    # Load suppliers
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

    # Load staff
    members = service.list_members(restaurant_id=restaurant_uuid)
    staff_list = []
    for member, membership in members[:max_staff]:
        staff_list.append({
            "id": str(member.id),
            "name": member.full_name,
            "username": member.username,
            "role": membership.role,
        })
    candidates["staff"] = staff_list

    return candidates


def _quick_match(message_text: str) -> ResolvedIntent | None:
    """Quick pattern matching for common commands - skip LLM call."""
    text_lower = message_text.strip().lower()
    text_clean = text_lower.rstrip("!?.,")

    # Greetings and menu requests
    if text_lower in ("/menu", "/help", "?") or text_clean in (
        "menu", "help", "what can you do",
        "hi", "hello", "hey", "hii", "hiii", "yo", "sup",
        "good morning", "good afternoon", "good evening",
        "hi there", "hello there", "hey there",
    ):
        return ResolvedIntent(intent=Intent.SHOW_MENU, confidence=1.0)

    # Cancel
    if text_lower in ("/cancel", "cancel", "nevermind", "stop"):
        return ResolvedIntent(intent=Intent.CANCEL, confidence=1.0)

    # Confirm
    if text_lower in ("/confirm", "confirm", "yes", "looks good", "save", "ok"):
        return ResolvedIntent(intent=Intent.CONFIRM_UPLOAD, confidence=1.0)

    return None


def resolve_intent(
    *,
    db: Session,
    user: User,
    message_text: str,
    history: list[dict[str, str]] | None = None,
    active_restaurant_id: str | None = None,
    active_supplier_id: str | None = None,
    has_file: bool = False,
    file_kind: str | None = None,
    settings: Settings,
) -> ResolvedIntent:
    """
    Classify intent AND resolve all entity names to UUIDs in a single LLM call.

    Args:
        db: Database session
        user: Current user
        message_text: The user's message text
        history: Recent conversation history
        active_restaurant_id: Currently active restaurant (from context)
        active_supplier_id: Currently active supplier (from context)
        has_file: Whether the message includes a file
        file_kind: Type of file if present
        settings: App settings

    Returns:
        ResolvedIntent with fully resolved params
    """
    # Quick match for common commands
    quick_result = _quick_match(message_text)
    if quick_result is not None:
        return quick_result

    # Build entity candidates
    candidates = _build_candidates(
        db=db,
        user_id=user.id,
        active_restaurant_id=active_restaurant_id,
    )

    # Build user prompt
    user_prompt_parts = []

    # Context
    context_info = {}
    if active_restaurant_id:
        context_info["active_restaurant_id"] = active_restaurant_id
    if active_supplier_id:
        context_info["active_supplier_id"] = active_supplier_id
    if context_info:
        user_prompt_parts.append(f"Active context: {json.dumps(context_info)}")

    # History
    history_text = _format_history(history)
    if history_text:
        user_prompt_parts.append(f"Recent conversation:\n{history_text}")

    # Entity candidates
    user_prompt_parts.append(f"Entity candidates:\n{json.dumps(candidates, indent=2)}")

    # File info
    if has_file:
        user_prompt_parts.append(f"[User uploaded a file: {file_kind or 'unknown type'}]")

    # User message
    user_prompt_parts.append(f"User message: {message_text}")

    user_prompt = "\n\n".join(user_prompt_parts)

    # Get model
    model = get_intent_model(settings)
    resolver_settings = (
        settings.model_copy(update={"openai_model": model})
        if model != settings.openai_model
        else settings
    )

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=resolver_settings,
            messages=[
                {"role": "system", "content": RESOLVER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except OpenAIError as e:
        logger.exception("intent_resolution_failed", extra={"error": str(e)})
        return ResolvedIntent(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            model=model,
        )

    # Extract response
    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", model)

    content: str | None = None
    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Empty content")
        content = content.strip()

        # Handle markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.startswith("```"))

        parsed = json.loads(content)
        intent_str = parsed.get("intent", "unknown")
        params = parsed.get("params", {})
        confidence = float(parsed.get("confidence", 0.5))
        needs_clarification = bool(parsed.get("needs_clarification", False))
        clarification_message = parsed.get("clarification_message")

        # Map to Intent enum
        try:
            intent = Intent(intent_str)
        except ValueError:
            intent = Intent.UNKNOWN

        return ResolvedIntent(
            intent=intent,
            params=params if isinstance(params, dict) else {},
            confidence=confidence,
            needs_clarification=needs_clarification,
            clarification_message=clarification_message if isinstance(clarification_message, str) else None,
            model=model_used if isinstance(model_used, str) else model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(
            "intent_resolution_parse_failed",
            extra={"error": str(e), "content": content},
        )
        return ResolvedIntent(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            model=model_used if isinstance(model_used, str) else model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
        )
