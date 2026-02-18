"""Priority 2: Button callback handler.

Handles inline keyboard button callbacks.

Wire format: `action:id:param`
- rev_u:{staging_hex} — Show upload review
- conf_u:{staging_hex} — Confirm upload → write to DB
- del_u:{staging_hex} — Cancel upload
- ed_row:{staging_hex}:{idx} — Prompt edit for item at index
- list_p:{type}:{page} — Paginate a list
- res_h:{handshake_hex}:{answer} — Resolve handshake question
- set_sup:{staging_hex}:{supplier_hex} — Link supplier to staging record
- new_sup:{staging_hex} — Create new supplier from staging's parsed name

Rules:
- All button handlers are strictly deterministic. No LLM calls.
- If referenced ID no longer exists → answer callback with alert.
- After state-changing actions, edit the original message to reflect final state.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.inventory_service import InventoryService
from app.services.restaurant_service import RestaurantService
from app.telegram.bot_api import answer_callback_query, edit_message_text, send_message
from app.telegram.keyboards import hex_to_uuid

logger = logging.getLogger(__name__)


def _parse_callback_data(data: str) -> tuple[str, list[str]]:
    """Parse callback data into (action, params).

    Examples:
        "rev_u:abc123" → ("rev_u", ["abc123"])
        "ed_row:abc123:2" → ("ed_row", ["abc123", "2"])
        "list_p:suppliers:1" → ("list_p", ["suppliers", "1"])
    """
    parts = data.split(":")
    action = parts[0] if parts else ""
    params = parts[1:] if len(parts) > 1 else []
    return action, params


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Dispatch button callbacks to appropriate handlers.

    Args:
        update: Telegram update dict (must contain callback_query)
        user: User ORM object
        db: Database session
        ctx_svc: ContextService for managing user.context
        settings: App settings
    """
    callback_query = update.get("callback_query", {})
    chat_id = user.chat_id
    callback_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")

    if not data:
        logger.warning("buttons: empty callback data")
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
        return

    action, params = _parse_callback_data(data)

    # --- rev_u: Review upload ---
    if action == "rev_u":
        if len(params) < 1:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex = params[0]
        try:
            staging_id = hex_to_uuid(staging_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        # TODO: Load staging record and render review
        answer_callback_query(
            callback_id=callback_id,
            text="Upload review (not yet implemented)",
            settings=settings,
        )
        return

    # --- conf_u: Confirm upload (invoice → inventory) ---
    if action == "conf_u":
        if len(params) < 1:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex = params[0]
        try:
            staging_id = hex_to_uuid(staging_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        staging = db.get(FileProcessingStaging, staging_id)
        if staging is None:
            answer_callback_query(
                callback_id=callback_id, text="Upload not found.", settings=settings
            )
            return

        # Authorization: uploader or active member of the restaurant.
        is_owner = staging.uploaded_by == user.id
        is_member = RestaurantService(db).user_membership_exists(
            restaurant_id=staging.restaurant_id, user_id=user.id
        )
        if not (is_owner or is_member):
            answer_callback_query(
                callback_id=callback_id, text="Not authorized.", settings=settings
            )
            return

        if staging.status != "pending_review":
            answer_callback_query(
                callback_id=callback_id,
                text="This upload has already been processed.",
                settings=settings,
            )
            return

        if staging.document_type != "invoice":
            answer_callback_query(
                callback_id=callback_id,
                text="Only invoice uploads can be confirmed here.",
                settings=settings,
            )
            return

        extracted = staging.extracted_data_json or {}
        raw_items = extracted.get("line_items", [])

        # Validate each line item: must have a non-empty name and a positive qty.
        line_items = []
        for li in raw_items:
            name = str(li.get("name") or "").strip()
            try:
                qty = float(li["qty"])
            except (KeyError, TypeError, ValueError):
                qty = 0.0
            if name and qty > 0:
                line_items.append(li)

        if not line_items:
            answer_callback_query(
                callback_id=callback_id,
                text="No valid line items found in this upload.",
                settings=settings,
            )
            return

        restaurant_id = staging.restaurant_id
        inv_svc = InventoryService(db)

        # Build resolutions: fuzzy-match at ≥0.8 → use existing item.
        # No match ≥0.8 → auto-create as new inventory item.
        resolutions: dict[str, object] = {}
        for li in line_items:
            name = li.get("name", "")
            if not name:
                continue
            matches = inv_svc.fuzzy_match_item(
                restaurant_id=restaurant_id, name=name, threshold=0.8
            )
            if matches:
                matched_item, _ = matches[0]
                resolutions[name] = matched_item.id
            else:
                new_item, _ = inv_svc.get_or_create_item(
                    restaurant_id=restaurant_id,
                    name=name,
                    unit=li.get("unit"),
                )
                resolutions[name] = new_item.id

        count = inv_svc.confirm_invoice(
            restaurant_id=restaurant_id,
            user_id=user.id,
            staging_id=staging_id,
            line_items=line_items,
            resolutions=resolutions,
        )

        staging.status = "confirmed"
        ctx_svc.set_fields(user, active_staging_id=None)
        db.commit()

        if message_id:
            edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"✅ Invoice confirmed — {count} item{'s' if count != 1 else ''} added to inventory.",
                settings=settings,
            )
        answer_callback_query(
            callback_id=callback_id, text="Invoice confirmed!", settings=settings
        )
        return

    # --- del_u: Delete/cancel upload ---
    if action == "del_u":
        if len(params) < 1:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex = params[0]
        try:
            staging_id = hex_to_uuid(staging_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        # TODO: Implement delete logic
        # 1. Set staging.status='cancelled'
        # 2. Clear user.context.active_staging_id
        # 3. Edit original message

        answer_callback_query(
            callback_id=callback_id,
            text="Upload cancelled (not yet implemented)",
            settings=settings,
        )
        return

    # --- ed_row: Edit row ---
    if action == "ed_row":
        if len(params) < 2:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex, idx_str = params[0], params[1]
        try:
            staging_id = hex_to_uuid(staging_hex)
            idx = int(idx_str)
        except (ValueError, TypeError):
            answer_callback_query(
                callback_id=callback_id, text="Invalid parameters", settings=settings
            )
            return

        # TODO: Implement edit row logic
        # 1. Load item at extracted_data_json.items[idx]
        # 2. Store edit_idx in user.context
        # 3. Prompt user for correction

        answer_callback_query(
            callback_id=callback_id,
            text=f"Edit item #{idx + 1} (not yet implemented)",
            settings=settings,
        )
        return

    # --- list_p: Pagination ---
    if action == "list_p":
        if len(params) < 2:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        list_type, page_str = params[0], params[1]
        try:
            page = int(page_str)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid page", settings=settings
            )
            return

        # TODO: Implement pagination for different list types
        answer_callback_query(
            callback_id=callback_id,
            text=f"Page {page} of {list_type} (not yet implemented)",
            settings=settings,
        )
        return

    # --- res_h: Resolve handshake ---
    if action == "res_h":
        if len(params) < 2:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        handshake_hex, answer = params[0], params[1]
        try:
            handshake_id = hex_to_uuid(handshake_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        # TODO: Implement handshake resolution
        answer_callback_query(
            callback_id=callback_id,
            text=f"Handshake resolved: {answer} (not yet implemented)",
            settings=settings,
        )
        return

    # --- set_sup: Set supplier on staging ---
    if action == "set_sup":
        if len(params) < 2:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex, supplier_hex = params[0], params[1]
        try:
            staging_id = hex_to_uuid(staging_hex)
            supplier_id = hex_to_uuid(supplier_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        # TODO: Implement supplier linking
        answer_callback_query(
            callback_id=callback_id,
            text="Supplier linked (not yet implemented)",
            settings=settings,
        )
        return

    # --- new_sup: Create new supplier ---
    if action == "new_sup":
        if len(params) < 1:
            answer_callback_query(
                callback_id=callback_id, text="Invalid callback", settings=settings
            )
            return

        staging_hex = params[0]
        try:
            staging_id = hex_to_uuid(staging_hex)
        except ValueError:
            answer_callback_query(
                callback_id=callback_id, text="Invalid ID", settings=settings
            )
            return

        # TODO: Implement new supplier creation
        answer_callback_query(
            callback_id=callback_id,
            text="New supplier (not yet implemented)",
            settings=settings,
        )
        return

    # Unknown action
    logger.warning("buttons: unknown action %s", action)
    answer_callback_query(
        callback_id=callback_id,
        text="Unknown action",
        settings=settings,
    )
