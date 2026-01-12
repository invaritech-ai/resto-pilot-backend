"""
Intent classifier for the static bot.

Uses a cheap/fast LLM to classify user messages into supported intents
and extract parameters inline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ai.model_config import get_intent_model
from app.ai.openai_client import (
    OpenAIError,
    chat_completions_create_with_http_info,
)
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.core.config import Settings

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    """Supported intents for the static bot."""

    # Profile
    VIEW_PROFILE = "view_profile"
    UPDATE_NAME = "update_name"
    UPDATE_PHONE = "update_phone"

    # Outlet (Restaurant)
    LIST_OUTLETS = "list_outlets"
    ADD_OUTLET = "add_outlet"
    UPDATE_OUTLET = "update_outlet"
    LIST_STAFF = "list_staff"
    ADD_STAFF = "add_staff"
    REVOKE_STAFF = "revoke_staff"

    # Supplier
    LIST_SUPPLIERS = "list_suppliers"
    ADD_SUPPLIER = "add_supplier"
    UPDATE_SUPPLIER = "update_supplier"
    VIEW_SUPPLIER = "view_supplier"
    VIEW_SUPPLIER_PRICE_LIST = "view_supplier_price_list"
    VIEW_SUPPLIER_ITEMS = "view_supplier_items"

    # Inventory
    LIST_INVENTORY = "list_inventory"
    ADD_INVENTORY = "add_inventory"
    UPDATE_INVENTORY = "update_inventory"
    LOG_INVENTORY_USAGE = "log_inventory_usage"
    LIST_LOCATIONS = "list_locations"
    ADD_LOCATION = "add_location"

    # Invoices
    LIST_INVOICES = "list_invoices"
    VIEW_INVOICE = "view_invoice"

    # File Upload
    UPLOAD_PRICE_LIST = "upload_price_list"
    UPLOAD_INVOICE = "upload_invoice"
    CONFIRM_UPLOAD = "confirm_upload"

    # Navigation
    SHOW_MENU = "show_menu"
    CANCEL = "cancel"

    # Unknown / Can't help
    UNKNOWN = "unknown"


# Parameters expected for each intent
INTENT_PARAMS: dict[Intent, list[str]] = {
    Intent.VIEW_PROFILE: [],
    Intent.UPDATE_NAME: ["name"],
    Intent.UPDATE_PHONE: ["phone"],
    Intent.LIST_OUTLETS: [],
    Intent.ADD_OUTLET: ["name"],
    Intent.UPDATE_OUTLET: ["restaurant_id", "name"],
    Intent.LIST_STAFF: ["restaurant_id"],
    Intent.ADD_STAFF: ["restaurant_id", "role"],
    Intent.REVOKE_STAFF: ["restaurant_id", "user_id"],
    Intent.LIST_SUPPLIERS: ["restaurant_id"],
    Intent.ADD_SUPPLIER: ["restaurant_id", "name"],
    Intent.UPDATE_SUPPLIER: ["supplier_id", "name"],
    Intent.VIEW_SUPPLIER: ["supplier_id"],
    Intent.VIEW_SUPPLIER_PRICE_LIST: ["supplier_id"],
    Intent.VIEW_SUPPLIER_ITEMS: ["supplier_id"],
    Intent.LIST_INVENTORY: ["restaurant_id"],
    Intent.ADD_INVENTORY: ["restaurant_id", "product_name", "quantity", "unit"],
    Intent.UPDATE_INVENTORY: ["batch_id", "quantity"],
    Intent.LOG_INVENTORY_USAGE: ["batch_id", "quantity", "reason"],
    Intent.LIST_LOCATIONS: ["restaurant_id"],
    Intent.ADD_LOCATION: ["restaurant_id", "name"],
    Intent.LIST_INVOICES: ["restaurant_id"],
    Intent.VIEW_INVOICE: ["invoice_id"],
    Intent.UPLOAD_PRICE_LIST: ["file_id", "restaurant_id"],
    Intent.UPLOAD_INVOICE: ["file_id", "restaurant_id"],
    Intent.CONFIRM_UPLOAD: ["staging_id"],
    Intent.SHOW_MENU: [],
    Intent.CANCEL: [],
    Intent.UNKNOWN: [],
}


@dataclass
class ClassifiedIntent:
    """Result of intent classification."""

    intent: Intent
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    missing_params: list[str] = field(default_factory=list)

    # Telemetry
    model: str = ""
    latency_ms: int = 0
    generation_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT = """You are an intent classifier for a restaurant management bot.

Your job is to:
1. Classify the user's message into one of the supported intents
2. Extract any parameters mentioned in the message
3. Return structured JSON

## Supported Intents

### Profile Management
- view_profile: User wants to see their profile (name, phone)
- update_name: User wants to change their name. Extract "name" parameter.
- update_phone: User wants to change their phone. Extract "phone" parameter.

### Outlet/Restaurant Management
- list_outlets: User wants to see their outlets/restaurants
- add_outlet: User wants to create a new outlet. Extract "name" parameter.
- update_outlet: User wants to rename an outlet. Extract "restaurant_id" and/or "name".
- list_staff: User wants to see staff members. Extract "restaurant_id" if mentioned.
- add_staff: User wants to invite staff. Extract "restaurant_id" and optionally "role" (staff/owner).
- revoke_staff: User wants to remove staff. Extract "restaurant_id" and "user_id" or user name.

### Supplier Management
- list_suppliers: User wants to see suppliers. Extract "restaurant_id" if mentioned.
- add_supplier: User wants to add a supplier. Extract "name" and optionally "restaurant_id".
- update_supplier: User wants to update supplier details. Extract "supplier_id" and fields.
- view_supplier: User wants to see supplier details. Extract "supplier_id" or supplier name.
- view_supplier_price_list: User wants to see supplier's current prices. Extract "supplier_id".
- view_supplier_items: User wants to see items/products a supplier offers. Extract "supplier_id".

### Inventory Management
- list_inventory: User wants to see inventory. Extract "restaurant_id" if mentioned.
- add_inventory: User wants to record received inventory.
- update_inventory: User wants to adjust inventory quantity.
- log_inventory_usage: User wants to log usage, waste, or consumption. Extract "batch_id", "quantity", "reason".
- list_locations: User wants to see storage locations. Extract "restaurant_id" if mentioned.
- add_location: User wants to add a storage location. Extract "restaurant_id", "name".

### Invoices
- list_invoices: User wants to see past invoices. Extract "restaurant_id" if mentioned.
- view_invoice: User wants to see invoice details. Extract "invoice_id".

### File Processing
- upload_price_list: User uploaded a price list file
- upload_invoice: User uploaded an invoice file
- confirm_upload: User wants to confirm processed file data (/confirm, "yes", "looks good")

### Navigation
- show_menu: User greets or wants to see available options ("hi", "hello", "menu", "help", "what can you do")
- cancel: User wants to cancel current operation ("cancel", "nevermind", "/cancel")

### Unknown
- unknown: Message doesn't match any supported intent. Use this for off-topic requests.

## Context Awareness

You will receive:
- The user's current message
- Recent conversation history (if any)
- Current active operation context (if any)
- Active outlet/restaurant context (if the user has selected one)

**IMPORTANT**: IDs are internal identifiers. Users will not provide IDs. Never invent IDs.
If the user mentions a name (supplier, outlet, staff), include the raw name in params.

**IMPORTANT**: If the context includes an "active_outlet" with a name, use that outlet's ID as "restaurant_id" for any action that requires it, unless the user explicitly mentions a different outlet.

For example:
- Context: {"active_outlet": {"id": "abc-123", "name": "Main Restaurant"}}
- User says: "show suppliers"
- You should return: {"intent": "list_suppliers", "params": {"restaurant_id": "abc-123"}}

If there's an active operation awaiting parameters, interpret the user's message in that context.
For example, if we asked "What's the supplier's name?" and user says "Fresh Farms", that's providing the name parameter.

## Output Format

Return ONLY valid JSON:
{
  "intent": "intent_name",
  "params": {"param1": "value1", "param2": "value2"},
  "confidence": 0.95
}

- confidence: 0.0 to 1.0, how confident you are
- params: only include parameters you can extract from the message
- For unknown intent, return {"intent": "unknown", "params": {}, "confidence": 0.9}
"""


def classify_intent(
    *,
    message_text: str,
    settings: Settings,
    has_file: bool = False,
    file_kind: str | None = None,
    context: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> ClassifiedIntent:
    """
    Classify user message into a supported intent.

    Args:
        message_text: The user's message text
        settings: App settings
        has_file: Whether the message includes a file
        file_kind: Type of file if present (photo, document, etc)
        context: Active operation context (operation name, pending params, collected values)
        history: Recent conversation history for context

    Returns:
        ClassifiedIntent with intent, params, confidence, and telemetry
    """
    # Quick checks for command shortcuts
    text_lower = message_text.strip().lower()
    # Also strip common punctuation for matching
    text_clean = text_lower.rstrip("!?.,")

    # Greetings and menu requests -> show menu
    if text_lower in ("/menu", "/help", "?") or text_clean in (
        "menu", "help", "what can you do",
        "hi", "hello", "hey", "hii", "hiii", "yo", "sup",
        "good morning", "good afternoon", "good evening",
        "hi there", "hello there", "hey there",
    ):
        return ClassifiedIntent(intent=Intent.SHOW_MENU, confidence=1.0)

    if text_lower in ("/cancel", "cancel", "nevermind", "stop"):
        return ClassifiedIntent(intent=Intent.CANCEL, confidence=1.0)

    if text_lower in ("/confirm", "confirm", "yes", "looks good", "save", "ok"):
        return ClassifiedIntent(intent=Intent.CONFIRM_UPLOAD, confidence=1.0)

    if any(kw in text_lower for kw in ("price list", "pricelist", "rate card")):
        return ClassifiedIntent(intent=Intent.UPLOAD_PRICE_LIST, confidence=0.9)

    # Build user prompt with context
    user_prompt_parts = []

    if context:
        context_str = json.dumps(context, indent=2)
        user_prompt_parts.append(f"Active operation context:\n{context_str}")

    if history:
        history_lines = []
        for msg in history[-20:]:
            role = msg.get("role", "")
            content = msg.get("content", "")[:200]
            if role and content:
                history_lines.append(f"{role}: {content}")
        if history_lines:
            user_prompt_parts.append(
                "Recent conversation:\n" + "\n".join(history_lines)
            )

    # Include file info
    if has_file:
        file_info = f"[User uploaded a file: {file_kind or 'unknown type'}]"
        user_prompt_parts.append(file_info)

    user_prompt_parts.append(f"User message: {message_text}")
    user_prompt = "\n\n".join(user_prompt_parts)

    # Get the gate/cheap model
    intent_model = get_intent_model(settings)
    gate_settings = (
        settings.model_copy(update={"openai_model": intent_model})
        if intent_model != settings.openai_model
        else settings
    )

    try:
        data, headers, latency_ms = chat_completions_create_with_http_info(
            settings=gate_settings,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except OpenAIError as e:
        logger.exception("intent_classification_failed", extra={"error": str(e)})
        return ClassifiedIntent(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            model=intent_model,
        )

    # Extract response
    usage = extract_openrouter_usage(data)
    generation_id = extract_openrouter_generation_id(headers=headers, data=data)
    model_used = data.get("model", intent_model)

    content: str | None = None
    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Empty content")
        content = content.strip()

        # Parse JSON - handle markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.startswith("```"))

        parsed = json.loads(content)
        intent_str = parsed.get("intent", "unknown")
        params = parsed.get("params", {})
        confidence = float(parsed.get("confidence", 0.5))

        # Map to Intent enum
        try:
            intent = Intent(intent_str)
        except ValueError:
            intent = Intent.UNKNOWN

        # Calculate missing params
        required_params = INTENT_PARAMS.get(intent, [])
        missing_params = [
            p for p in required_params if p not in params or not params[p]
        ]

        return ClassifiedIntent(
            intent=intent,
            params=params if isinstance(params, dict) else {},
            confidence=confidence,
            missing_params=missing_params,
            model=model_used if isinstance(model_used, str) else intent_model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(
            "intent_classification_parse_failed",
            extra={"error": str(e), "content": content},
        )
        return ClassifiedIntent(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            model=model_used if "model_used" in dir() else intent_model,
            latency_ms=latency_ms,
            generation_id=generation_id,
            usage=usage if "usage" in dir() and isinstance(usage, dict) else {},
        )


def get_missing_param_prompt(intent: Intent, missing_param: str) -> str:
    """Get the prompt to ask user for a missing parameter."""
    prompts: dict[tuple[Intent, str], str] = {
        # Profile
        (Intent.UPDATE_NAME, "name"): "What would you like your name to be?",
        (
            Intent.UPDATE_PHONE,
            "phone",
        ): "What's your phone number? (include country code, e.g. +1 415 555 0101)",
        # Outlet
        (Intent.ADD_OUTLET, "name"): "What would you like to name this outlet?",
        (
            Intent.UPDATE_OUTLET,
            "restaurant_id",
        ): "Which outlet would you like to update?",
        (Intent.UPDATE_OUTLET, "name"): "What's the new name for this outlet?",
        (
            Intent.LIST_STAFF,
            "restaurant_id",
        ): "Which outlet's staff would you like to see?",
        (
            Intent.ADD_STAFF,
            "restaurant_id",
        ): "Which outlet would you like to invite staff to?",
        (Intent.REVOKE_STAFF, "restaurant_id"): "Which outlet?",
        (
            Intent.REVOKE_STAFF,
            "user_id",
        ): "Which staff member would you like to remove?",
        # Supplier
        (Intent.ADD_SUPPLIER, "name"): "What's the supplier's name?",
        (Intent.ADD_SUPPLIER, "restaurant_id"): "Which outlet is this supplier for?",
        (
            Intent.UPDATE_SUPPLIER,
            "supplier_id",
        ): "Which supplier would you like to update?",
        (Intent.VIEW_SUPPLIER, "supplier_id"): "Which supplier would you like to view?",
        (
            Intent.VIEW_SUPPLIER_PRICE_LIST,
            "supplier_id",
        ): "Which supplier's price list would you like to see?",
        (
            Intent.VIEW_SUPPLIER_ITEMS,
            "supplier_id",
        ): "Which supplier's items would you like to see?",
        (
            Intent.LIST_SUPPLIERS,
            "restaurant_id",
        ): "Which outlet's suppliers would you like to see?",
        # Inventory
        (
            Intent.LIST_INVENTORY,
            "restaurant_id",
        ): "Which outlet's inventory would you like to see?",
        (Intent.ADD_INVENTORY, "restaurant_id"): "Which outlet is this inventory for?",
        (Intent.ADD_INVENTORY, "product_name"): "What product did you receive?",
        (Intent.ADD_INVENTORY, "quantity"): "How much did you receive?",
        (Intent.ADD_INVENTORY, "unit"): "What unit? (kg, pieces, liters, etc)",
        (
            Intent.UPDATE_INVENTORY,
            "batch_id",
        ): "Which inventory batch would you like to update?",
        (Intent.UPDATE_INVENTORY, "quantity"): "What's the new quantity?",
        (
            Intent.LOG_INVENTORY_USAGE,
            "batch_id",
        ): "Which inventory batch are you logging usage for?",
        (Intent.LOG_INVENTORY_USAGE, "quantity"): "How much was used/wasted?",
        (
            Intent.LOG_INVENTORY_USAGE,
            "reason",
        ): "What's the reason? (usage, waste, expired, etc)",
        (
            Intent.LIST_LOCATIONS,
            "restaurant_id",
        ): "Which outlet's locations would you like to see?",
        (Intent.ADD_LOCATION, "restaurant_id"): "Which outlet is this location for?",
        (
            Intent.ADD_LOCATION,
            "name",
        ): "What would you like to name this location? (e.g., Main Fridge, Dry Storage)",
        # Invoices
        (
            Intent.LIST_INVOICES,
            "restaurant_id",
        ): "Which outlet's invoices would you like to see?",
        (Intent.VIEW_INVOICE, "invoice_id"): "Which invoice would you like to view?",
        # File
        (
            Intent.UPLOAD_PRICE_LIST,
            "restaurant_id",
        ): "Which outlet is this price list for?",
        (Intent.UPLOAD_INVOICE, "restaurant_id"): "Which outlet is this invoice for?",
        (
            Intent.CONFIRM_UPLOAD,
            "staging_id",
        ): "Which upload would you like to confirm?",
    }

    return prompts.get(
        (intent, missing_param),
        f"Please provide the {missing_param.replace('_', ' ')}.",
    )
