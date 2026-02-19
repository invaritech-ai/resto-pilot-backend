"""Priority 2: Button callback handler.

Wire format: `action:id:param`

Implemented actions:
  doc_type:{staging_hex}:{invoice|price_list}
      Set document type → dispatch Celery OCR task → edit "Processing..."

  conf_u:{staging_hex}
      Confirm upload:
        - Supplier gate: supplier_id must be set first
        - Price list: PriceService.confirm_price_list()
        - Invoice: 3-tier fuzzy gate → auto-confirm or show resolution hub

  del_u:{staging_hex}
      Cancel upload → status=cancelled, clear context, edit message

  set_sup:{staging_hex}:{supplier_hex}
      Link existing supplier to staging → update review message

  new_sup:{staging_hex}
      Create new supplier from extracted name → link → update review message

  type_sup:{staging_hex}
      Prompt for typed supplier name → resolve or create via next text input

  use_match:{staging_hex}:{idx}:{item_hex}
      Accept fuzzy match for item[idx] → update resolution hub

  mk_item:{staging_hex}:{idx}
      Create new inventory item for item[idx] → update resolution hub

  skip_item:{staging_hex}:{idx}
      Skip item[idx] (not added to inventory) → update resolution hub

  ed_row:{staging_hex}:{idx}
      Show field picker for item[idx] (name/qty/unit/price buttons)

  ed_fld:{staging_hex}:{idx}:{field}
      Store editing state in context, prompt user to type new value

Stubs (not yet implemented):
  res_h
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.inventory_service import InventoryService
from app.services.price_service import PriceService
from app.services.restaurant_service import RestaurantService
from app.services.staging_service import StagingService
from app.services.supplier_service import SupplierService
from app.telegram.bot_api import answer_callback_query, edit_message_text, send_message, send_message_with_keyboard
from app.telegram.renderer import format_with_emoji, format_supplier_name, format_price_monospace, format_footer
from app.telegram.keyboards import (
    cb_confirm_upload,
    cb_delete_upload,
    cb_edit_field,
    cb_mk_item,
    cb_open_upload,
    cb_pick_currency,
    cb_set_currency,
    cb_skip_item,
    cb_stock_cancel,
    cb_stock_conf,
    cb_stock_new,
    cb_use_match,
    hex_to_uuid,
    make_button,
)

logger = logging.getLogger(__name__)

_MAX_HUB_ITEMS = 10  # show at most 10 pending items in resolution hub
_AUTHZ_DENIED_TEXT = "Action unavailable."


def _deny_callback_authz(*, callback_id: str, settings: Settings) -> None:
    answer_callback_query(
        callback_id=callback_id,
        text=_AUTHZ_DENIED_TEXT,
        show_alert=True,
        settings=settings,
    )


def _membership_flags(*, db: Session, restaurant_id, user_id) -> tuple[bool, bool]:
    is_member = RestaurantService(db).user_membership_exists(
        restaurant_id=restaurant_id,
        user_id=user_id,
    )
    if not is_member:
        return False, False

    owner_flag = db.scalar(
        select(RestaurantUser.is_owner).where(
            RestaurantUser.restaurant_id == restaurant_id,
            RestaurantUser.user_id == user_id,
            RestaurantUser.is_active.is_(True),
        )
    )
    is_owner = owner_flag if isinstance(owner_flag, bool) else False
    return True, is_owner


def _is_authorized_for_staging(
    *,
    db: Session,
    staging: FileProcessingStaging,
    user: User,
    require_uploader_or_owner: bool,
) -> bool:
    is_member, is_owner = _membership_flags(
        db=db,
        restaurant_id=staging.restaurant_id,
        user_id=user.id,
    )
    if not is_member:
        return False
    if not require_uploader_or_owner:
        return True
    return bool(is_owner or staging.uploaded_by == user.id)


def _authorize_staging_callback(
    *,
    db: Session,
    staging: FileProcessingStaging,
    user: User,
    callback_id: str,
    settings: Settings,
    require_uploader_or_owner: bool,
) -> bool:
    allowed = _is_authorized_for_staging(
        db=db,
        staging=staging,
        user=user,
        require_uploader_or_owner=require_uploader_or_owner,
    )
    if allowed:
        return True
    _deny_callback_authz(callback_id=callback_id, settings=settings)
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Dispatch button callbacks."""
    cq = update.get("callback_query", {})
    chat_id = user.chat_id
    callback_id = cq.get("id")
    data = cq.get("data", "")
    message = cq.get("message") or {}
    message_id = message.get("message_id")

    if not data:
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
        return

    parts = data.split(":")
    action = parts[0] if parts else ""
    params = parts[1:] if len(parts) > 1 else []

    dispatch = {
        "doc_type":    _handle_doc_type,
        "conf_u":      _handle_conf_u,
        "del_u":       _handle_del_u,
        "set_sup":     _handle_set_sup,
        "new_sup":     _handle_new_sup,
        "type_sup":    _handle_type_sup,
        "use_match":   _handle_use_match,
        "mk_item":     _handle_mk_item,
        "skip_item":   _handle_skip_item,
        "rev_p":       _handle_rev_page,
        "open_u":      _handle_open_u,
        "pick_cur":    _handle_pick_cur,
        "set_cur":     _handle_set_cur,
        "list_p":      _handle_list_page,
        "ed_row":      _handle_ed_row,
        "ed_fld":      _handle_ed_fld,
        "stock_conf":  _handle_stock_conf,
        "stock_new":   _handle_stock_new,
        "stock_cancel": _handle_stock_cancel,
    }

    handler = dispatch.get(action)
    if handler:
        handler(
            params=params,
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            callback_id=callback_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        return

    # Stubs
    if action == "rev_u":
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
    elif action == "res_h":
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
    else:
        logger.warning("buttons: unknown action %s", action)
        answer_callback_query(callback_id=callback_id, text="", settings=settings)


# ---------------------------------------------------------------------------
# doc_type handler
# ---------------------------------------------------------------------------


def _handle_doc_type(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    staging_hex, doc_type = params[0], params[1]
    if doc_type not in ("invoice", "price_list"):
        answer_callback_query(callback_id=callback_id, text="Unknown document type.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(staging_hex)
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status != "processing":
        answer_callback_query(
            callback_id=callback_id,
            text="This upload has already been classified.",
            settings=settings,
        )
        return

    # Set document type
    StagingService(db).set_document_type(staging_id, doc_type)
    db.commit()

    # Dispatch Celery OCR task
    from app.workers.ocr_tasks import process_file_task

    process_file_task.delay(staging_hex, doc_type, chat_id, message_id)

    label = "invoice" if doc_type == "invoice" else "price list"
    answer_callback_query(callback_id=callback_id, text="", settings=settings)

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ Got it! Processing your {label}…",
            settings=settings,
        )


# ---------------------------------------------------------------------------
# conf_u handler
# ---------------------------------------------------------------------------


def _handle_conf_u(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status != "pending_review":
        answer_callback_query(
            callback_id=callback_id,
            text="This upload has already been processed.",
            show_alert=True,
            settings=settings,
        )
        return

    # Gate 1: supplier must be set
    if staging.supplier_id is None:
        answer_callback_query(
            callback_id=callback_id,
            text="⚠️ Please confirm the supplier first.",
            show_alert=True,
            settings=settings,
        )
        return

    # Price list: direct confirm
    if staging.document_type == "price_list":
        # Gate 2: currency must be set (handwritten invoices never have it printed)
        extracted_data = staging.extracted_data_json or {}
        if not extracted_data.get("currency"):
            answer_callback_query(
                callback_id=callback_id,
                text="⚠️ Please set the currency before confirming.",
                show_alert=True,
                settings=settings,
            )
            return

        try:
            count = PriceService(db).confirm_price_list(
                staging_id=staging_id,
                restaurant_id=staging.restaurant_id,
                user_id=user.id,
            )
        except ValueError as exc:
            answer_callback_query(
                callback_id=callback_id, text=str(exc), show_alert=True, settings=settings
            )
            return

        # Get price changes for insight card
        from app.services.emoji_taxonomy import emoji_taxonomy
        from app.services.money import to_display
        
        extracted_data = staging.extracted_data_json or {}
        line_items = extracted_data.get("line_items", [])
        currency = extracted_data.get("currency", "")
        
        price_changes = PriceService(db).get_price_changes_for_supplier(
            supplier_id=staging.supplier_id,
            restaurant_id=staging.restaurant_id,
            new_items=line_items,
            currency=currency,
        )
        
        staging.status = "confirmed"
        ctx_svc.set_fields(
            user,
            active_staging_id=None,
            review_message_id=None,
            pending_item_resolutions=None,
        )
        db.commit()

        # Build insight card
        insight_lines = []
        
        if price_changes:
            # Group changes
            new_items = [c for c in price_changes if c["is_new"]]
            increases = [c for c in price_changes if not c["is_new"] and c.get("percent_change") is not None and c["percent_change"] > 10]
            decreases = [c for c in price_changes if not c["is_new"] and c.get("percent_change") is not None and c["percent_change"] < -10]
            minor_changes = [c for c in price_changes if not c["is_new"] and c.get("percent_change") is not None and -10 <= c["percent_change"] <= 10]
            
            # Get supplier name for header
            from sqlalchemy import select
            from app.db.models.suppliers import Supplier
            supplier = db.scalar(select(Supplier).where(Supplier.id == staging.supplier_id))
            supplier_name = supplier.name if supplier else "Supplier"
            
            insight_lines.append(f"✅ Price list confirmed — {count} price{'s' if count != 1 else ''} saved.\n")
            insight_lines.append(f"📈 Price Insights for {supplier_name}:\n")
            
            # New items
            if new_items:
                insight_lines.append(f"🆕 New items ({len(new_items)}):")
                for change in new_items[:5]:  # Limit to 5
                    item_name = emoji_taxonomy.format_with_emoji(change["item_name"])
                    price_display = to_display(change["new_price_minor"], change["new_price_exp"])
                    currency_symbol = change["new_currency"] or ""
                    insight_lines.append(f"  {item_name} — {currency_symbol}{price_display:.2f}")
                insight_lines.append("")
            
            # Significant increases
            if increases:
                insight_lines.append(f"🔺 Price increases >10% ({len(increases)}):")
                for change in increases[:5]:  # Limit to 5
                    item_name = emoji_taxonomy.format_with_emoji(change["item_name"])
                    old_price = to_display(change["old_price_minor"], change["old_price_exp"])
                    new_price = to_display(change["new_price_minor"], change["new_price_exp"])
                    percent = change["percent_change"]
                    currency_symbol = change["new_currency"] or ""
                    insight_lines.append(f"  {item_name} — {currency_symbol}{old_price:.2f} → {currency_symbol}{new_price:.2f} (+{percent:.0f}%)")
                insight_lines.append("")
            
            # Significant decreases
            if decreases:
                insight_lines.append(f"🔻 Price decreases >10% ({len(decreases)}):")
                for change in decreases[:5]:  # Limit to 5
                    item_name = emoji_taxonomy.format_with_emoji(change["item_name"])
                    old_price = to_display(change["old_price_minor"], change["old_price_exp"])
                    new_price = to_display(change["new_price_minor"], change["new_price_exp"])
                    percent = change["percent_change"]
                    currency_symbol = change["new_currency"] or ""
                    insight_lines.append(f"  {item_name} — {currency_symbol}{old_price:.2f} → {currency_symbol}{new_price:.2f} ({percent:.0f}%)")
                insight_lines.append("")
            
            # Minor changes (optional)
            if minor_changes and not (new_items or increases or decreases):
                insight_lines.append("✅ No significant price changes detected.")
            
            # Truncate if too long
            insight_text = "\n".join(insight_lines)
            if len(insight_text) > 4000:
                insight_text = "\n".join(insight_lines[:20]) + "\n\n... and more"
        else:
            insight_text = f"✅ Price list confirmed — {count} price{'s' if count != 1 else ''} saved."
        
        if message_id:
            edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=insight_text,
                settings=settings,
            )
        answer_callback_query(
            callback_id=callback_id, text="Price list confirmed!", settings=settings
        )
        return

    # Invoice: 3-tier item gate
    extracted = staging.extracted_data_json or {}
    raw_items = extracted.get("line_items", [])
    items = [
        li for li in raw_items
        if li.get("name") and float(li.get("qty") or 0) > 0
    ]

    if not items:
        answer_callback_query(
            callback_id=callback_id,
            text="No valid line items found.",
            show_alert=True,
            settings=settings,
        )
        return

    # Load or compute pending_item_resolutions
    ctx = ctx_svc.get(user)
    if not isinstance(ctx, dict):
        ctx = {}
    raw_resolutions = ctx.get("pending_item_resolutions")
    resolutions: dict = (
        dict(raw_resolutions) if isinstance(raw_resolutions, dict) else {}
    )

    inv_svc = InventoryService(db)

    if not resolutions:
        # First time: compute fuzzy matches for all items.
        # Items beyond _MAX_HUB_ITEMS cannot be shown/resolved by the user,
        # so auto-resolve them: use a strong match (>=0.8) or create new.
        for i, li in enumerate(items):
            name = li.get("name", "")
            matches = inv_svc.fuzzy_match_item(
                restaurant_id=staging.restaurant_id, name=name, threshold=0.5
            )
            if matches:
                best, score = matches[0]
                if score >= 0.8:
                    resolutions[str(i)] = str(best.id)  # auto-resolved
                elif i < _MAX_HUB_ITEMS:
                    resolutions[str(i)] = None  # needs user input in hub
                else:
                    resolutions[str(i)] = "new"  # beyond hub cap → auto-create
            else:
                if i < _MAX_HUB_ITEMS:
                    resolutions[str(i)] = None  # needs user input in hub
                else:
                    resolutions[str(i)] = "new"  # beyond hub cap → auto-create

        ctx_svc.set_fields(user, pending_item_resolutions=resolutions)
        db.flush()

    # Check if all resolved
    if _all_resolved(resolutions):
        _do_confirm_invoice(
            staging=staging,
            items=items,
            resolutions=resolutions,
            inv_svc=inv_svc,
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            chat_id=chat_id,
            message_id=message_id,
        )
        answer_callback_query(
            callback_id=callback_id, text="Invoice confirmed!", settings=settings
        )
        return

    # Build resolution hub and edit the review message
    hub_text, hub_keyboard = _build_resolution_hub(
        staging=staging,
        items=items,
        resolutions=resolutions,
        inv_svc=inv_svc,
    )
    db.commit()

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=hub_text,
            settings=settings,
            reply_markup=hub_keyboard,
        )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


# ---------------------------------------------------------------------------
# del_u handler
# ---------------------------------------------------------------------------


def _handle_del_u(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status in ("confirmed", "cancelled"):
        answer_callback_query(
            callback_id=callback_id,
            text="Already processed.",
            settings=settings,
        )
        return

    staging.status = "cancelled"
    ctx_svc.set_fields(
        user,
        active_staging_id=None,
        review_message_id=None,
        pending_item_resolutions=None,
    )
    db.commit()

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Upload cancelled.",
            settings=settings,
        )
    answer_callback_query(callback_id=callback_id, text="Upload cancelled.", settings=settings)


# ---------------------------------------------------------------------------
# set_sup handler
# ---------------------------------------------------------------------------


def _handle_set_sup(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    supplier_id = None
    supplier_ref = params[1]

    # Backward compatible:
    # - legacy payload: supplier_ref is supplier UUID hex
    # - compact payload: supplier_ref is fuzzy-match rank index
    try:
        supplier_id = hex_to_uuid(supplier_ref)
    except ValueError:
        try:
            rank = int(supplier_ref)
        except (ValueError, TypeError):
            answer_callback_query(callback_id=callback_id, text="Invalid selection.", settings=settings)
            return
        if rank < 0:
            answer_callback_query(callback_id=callback_id, text="Invalid selection.", settings=settings)
            return

        extracted = staging.extracted_data_json or {}
        supplier_name = (extracted.get("supplier") or "").strip()

        svc = SupplierService(db)
        from app.workers.ocr_tasks import _supplier_candidates_for_name

        matches = _supplier_candidates_for_name(
            svc=svc,
            name=supplier_name,
            restaurant_id=staging.restaurant_id,
        )
        if rank >= len(matches):
            answer_callback_query(
                callback_id=callback_id,
                text="Supplier option no longer available.",
                show_alert=True,
                settings=settings,
            )
            return
        supplier_id = matches[rank][0].id

    from sqlalchemy import select
    from app.db.models.suppliers import Supplier

    supplier = db.scalar(select(Supplier).where(Supplier.id == supplier_id))
    if supplier is None:
        answer_callback_query(callback_id=callback_id, text="Supplier not found.", settings=settings)
        return

    # Ensure supplier is linked to the restaurant
    svc = SupplierService(db)
    if not svc.is_linked(supplier_id=supplier_id, restaurant_id=staging.restaurant_id):
        try:
            svc.link(
                supplier_id=supplier_id,
                restaurant_id=staging.restaurant_id,
                user_id=user.id,
            )
        except Exception as exc:
            logger.warning("set_sup: link failed supplier_id=%s: %s", supplier_id, exc)
            answer_callback_query(
                callback_id=callback_id,
                text="⚠️ Failed to link supplier. Please try again.",
                show_alert=True,
                settings=settings,
            )
            return

    staging.supplier_id = supplier_id
    db.commit()

    if message_id:
        _edit_supplier_in_review(
            chat_id=chat_id,
            message_id=message_id,
            staging=staging,
            supplier=supplier,
            settings=settings,
        )
    answer_callback_query(
        callback_id=callback_id, text=f"Supplier set: {supplier.name}", settings=settings
    )


# ---------------------------------------------------------------------------
# new_sup handler
# ---------------------------------------------------------------------------


def _handle_new_sup(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    # Get supplier name from extracted data
    extracted = staging.extracted_data_json or {}
    supplier_name = (extracted.get("supplier") or "").strip()
    if not supplier_name:
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=str(staging_id),
            supplier_input_mode="create",
        )
        db.commit()
        send_message(
            chat_id=chat_id,
            text="Type the supplier name to create and link it to this upload.",
            settings=settings,
        )
        answer_callback_query(
            callback_id=callback_id,
            text="Enter supplier name",
            settings=settings,
        )
        return

    # Create supplier + link
    svc = SupplierService(db)
    supplier = svc.create(
        name=supplier_name,
        user_id=user.id,
        restaurant_id=staging.restaurant_id,
    )

    staging.supplier_id = supplier.id
    db.commit()

    if message_id:
        _edit_supplier_in_review(
            chat_id=chat_id,
            message_id=message_id,
            staging=staging,
            supplier=supplier,
            settings=settings,
        )
    answer_callback_query(
        callback_id=callback_id, text=f"Supplier created: {supplier.name}", settings=settings
    )


def _handle_type_sup(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Prompt user to type supplier name for this staging review."""
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status != "pending_review":
        answer_callback_query(
            callback_id=callback_id,
            text="This upload is no longer awaiting review.",
            show_alert=True,
            settings=settings,
        )
        return

    ctx_svc.set_fields(
        user,
        supplier_input_staging_id=str(staging_id),
        supplier_input_mode="resolve",
    )
    db.commit()

    send_message(
        chat_id=chat_id,
        text="Type the supplier name. I'll match existing suppliers first, then let you create a new one.",
        settings=settings,
    )
    answer_callback_query(callback_id=callback_id, text="Enter supplier name", settings=settings)


# ---------------------------------------------------------------------------
# use_match / mk_item / skip_item handlers (resolution hub)
# ---------------------------------------------------------------------------


def _handle_use_match(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Accept a fuzzy-match suggestion for item[idx]."""
    if len(params) < 3:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
        idx = int(params[1])
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    item_id = None
    match_ref = params[2]

    # Backward compatible:
    # - legacy payload: match_ref is item UUID hex
    # - compact payload: match_ref is fuzzy-match rank index
    try:
        item_id = hex_to_uuid(match_ref)
    except ValueError:
        try:
            rank = int(match_ref)
        except (ValueError, TypeError):
            answer_callback_query(callback_id=callback_id, text="Invalid selection.", settings=settings)
            return
        if rank < 0:
            answer_callback_query(callback_id=callback_id, text="Invalid selection.", settings=settings)
            return

        staging = db.get(FileProcessingStaging, staging_id)
        if staging is None:
            answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
            return

        if not _authorize_staging_callback(
            db=db,
            staging=staging,
            user=user,
            callback_id=callback_id,
            settings=settings,
            require_uploader_or_owner=True,
        ):
            return

        extracted = staging.extracted_data_json or {}
        items = [
            li for li in extracted.get("line_items", [])
            if li.get("name") and float(li.get("qty") or 0) > 0
        ]
        if idx < 0 or idx >= len(items):
            answer_callback_query(
                callback_id=callback_id,
                text="Item option no longer available.",
                show_alert=True,
                settings=settings,
            )
            return

        inv_svc = InventoryService(db)
        matches = inv_svc.fuzzy_match_item(
            restaurant_id=staging.restaurant_id,
            name=items[idx].get("name", ""),
            threshold=0.5,
        )
        if rank >= len(matches):
            answer_callback_query(
                callback_id=callback_id,
                text="Match option no longer available.",
                show_alert=True,
                settings=settings,
            )
            return
        item_id = matches[rank][0].id

    _update_resolution(
        staging_id=staging_id,
        idx=idx,
        resolution=str(item_id),
        user=user,
        db=db,
        ctx_svc=ctx_svc,
        settings=settings,
        callback_id=callback_id,
        chat_id=chat_id,
        message_id=message_id,
    )


def _handle_mk_item(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Create new inventory item for item[idx]."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
        idx = int(params[1])
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    _update_resolution(
        staging_id=staging_id,
        idx=idx,
        resolution="new",
        user=user,
        db=db,
        ctx_svc=ctx_svc,
        settings=settings,
        callback_id=callback_id,
        chat_id=chat_id,
        message_id=message_id,
    )


def _handle_skip_item(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Skip item[idx] — omit from inventory write."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
        idx = int(params[1])
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    _update_resolution(
        staging_id=staging_id,
        idx=idx,
        resolution="skip",
        user=user,
        db=db,
        ctx_svc=ctx_svc,
        settings=settings,
        callback_id=callback_id,
        chat_id=chat_id,
        message_id=message_id,
    )


def _update_resolution(
    *,
    staging_id,
    idx: int,
    resolution: str,
    user,
    db,
    ctx_svc,
    settings,
    callback_id,
    chat_id,
    message_id,
):
    """Update a single item resolution in context, then re-render or confirm."""
    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status != "pending_review":
        answer_callback_query(
            callback_id=callback_id, text="Already processed.", settings=settings
        )
        return

    ctx = ctx_svc.get(user)
    resolutions: dict = dict(ctx.get("pending_item_resolutions") or {})
    resolutions[str(idx)] = resolution
    ctx_svc.set_fields(user, pending_item_resolutions=resolutions)
    db.flush()

    extracted = staging.extracted_data_json or {}
    items = [
        li for li in extracted.get("line_items", [])
        if li.get("name") and float(li.get("qty") or 0) > 0
    ]

    if _all_resolved(resolutions):
        inv_svc = InventoryService(db)
        _do_confirm_invoice(
            staging=staging,
            items=items,
            resolutions=resolutions,
            inv_svc=inv_svc,
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
            chat_id=chat_id,
            message_id=message_id,
        )
        answer_callback_query(
            callback_id=callback_id, text="Invoice confirmed!", settings=settings
        )
        return

    # Not all resolved — re-render hub
    inv_svc = InventoryService(db)
    hub_text, hub_keyboard = _build_resolution_hub(
        staging=staging,
        items=items,
        resolutions=resolutions,
        inv_svc=inv_svc,
    )
    db.commit()

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=hub_text,
            settings=settings,
            reply_markup=hub_keyboard,
        )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


# ---------------------------------------------------------------------------
# Confirmation logic
# ---------------------------------------------------------------------------


def _all_resolved(resolutions: dict) -> bool:
    """Return True if no item is still None (pending)."""
    return all(v is not None for v in resolutions.values())


def _do_confirm_invoice(
    *,
    staging,
    items: list[dict],
    resolutions: dict,
    inv_svc: InventoryService,
    user,
    db,
    ctx_svc,
    settings,
    chat_id: int,
    message_id: int | None,
):
    """Run confirm_invoice with final resolutions, update staging, clear context."""
    # Build index → item_id map (index-keyed so duplicate names resolve independently)
    final: dict[int, _uuid_mod.UUID | None] = {}
    for i, li in enumerate(items):
        name = li.get("name", "")
        res = resolutions.get(str(i))
        if res is None or res == "skip":
            final[i] = None
        elif res == "new":
            new_item, _ = inv_svc.get_or_create_item(
                restaurant_id=staging.restaurant_id,
                name=name,
                unit=li.get("unit"),
            )
            final[i] = new_item.id
        else:
            try:
                final[i] = _uuid_mod.UUID(res)
            except ValueError:
                final[i] = None

    count = inv_svc.confirm_invoice(
        restaurant_id=staging.restaurant_id,
        user_id=user.id,
        staging_id=staging.id,
        line_items=items,
        resolutions=final,
    )

    staging.status = "confirmed"
    ctx_svc.set_fields(
        user,
        active_staging_id=None,
        review_message_id=None,
        pending_item_resolutions=None,
    )
    db.commit()

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"✅ Invoice confirmed — {count} item{'s' if count != 1 else ''} added to inventory.",
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Resolution hub builder
# ---------------------------------------------------------------------------


def _build_resolution_hub(
    *,
    staging: FileProcessingStaging,
    items: list[dict],
    resolutions: dict,
    inv_svc: InventoryService,
) -> tuple[str, dict]:
    """Build the resolution hub message text and keyboard.

    Shows resolved items with checkmarks; pending items with action buttons.
    """
    lines = ["⚠️ Resolve items to confirm:  (tap to map each item)\n"]
    keyboard_rows: list[list[dict]] = []
    staging_id = staging.id

    for i, li in enumerate(items[:_MAX_HUB_ITEMS]):
        name = li.get("name", "?")
        key = str(i)
        res = resolutions.get(key)

        if res is None:
            # Pending: get fuzzy match for this item
            lines.append(f"❓ {i + 1}. {name}")
            matches = inv_svc.fuzzy_match_item(
                restaurant_id=staging.restaurant_id, name=name, threshold=0.5
            )
            if matches:
                best, _score = matches[0]
                keyboard_rows.append([
                    make_button(
                        f"✅ Use '{best.name}'",
                        cb_use_match(staging_id, i, 0),
                    ),
                    make_button(
                        "➕ Create New",
                        cb_mk_item(staging_id, i),
                    ),
                ])
            else:
                keyboard_rows.append([
                    make_button(f"➕ Add '{name}'", cb_mk_item(staging_id, i)),
                    make_button("⬅️ Skip", cb_skip_item(staging_id, i)),
                ])
        elif res == "new":
            lines.append(f"➕ {i + 1}. {name}  → new item")
        elif res == "skip":
            lines.append(f"⬅️ {i + 1}. {name}  → skipped")
        else:
            lines.append(f"✓ {i + 1}. {name}")

    if len(items) > _MAX_HUB_ITEMS:
        lines.append(f"\n… +{len(items) - _MAX_HUB_ITEMS} more (processed automatically)")

    # Cancel button at bottom
    keyboard_rows.append([make_button("❌ Cancel", cb_delete_upload(staging_id))])

    return "\n".join(lines), {"inline_keyboard": keyboard_rows}


# ---------------------------------------------------------------------------
# Review message update helpers
# ---------------------------------------------------------------------------


_COMMON_CURRENCIES = ["HKD", "SGD", "USD", "EUR", "GBP", "AUD", "THB", "CNY"]


def _handle_open_u(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Re-open a pending_review staging record and re-send its review message."""
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    if staging.status != "pending_review":
        answer_callback_query(
            callback_id=callback_id,
            text="This upload is no longer awaiting review.",
            show_alert=True,
            settings=settings,
        )
        return

    supplier = None
    if staging.supplier_id:
        from sqlalchemy import select
        from app.db.models.suppliers import Supplier
        supplier = db.scalar(select(Supplier).where(Supplier.id == staging.supplier_id))

    extracted = staging.extracted_data_json or {}
    doc_type = staging.document_type or "invoice"

    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text
    text = _build_review_text(staging, extracted, supplier, doc_type, page=0)
    keyboard = _build_review_keyboard(staging, extracted, sup_buttons=None, page=0)

    msg_id = send_message_with_keyboard(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        settings=settings,
    )

    # Update context so confirm/edit buttons know the active staging + message
    ctx_svc.set_fields(user, active_staging_id=staging.id, review_message_id=msg_id)
    db.commit()

    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def _handle_pick_cur(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Show currency picker — edit the review message to show currency options."""
    if not params:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    # Build currency picker keyboard (2 per row)
    buttons = [
        make_button(code, cb_set_currency(staging_id, code))
        for code in _COMMON_CURRENCIES
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([make_button("❌ Cancel", cb_delete_upload(staging_id))])

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="💱 Select currency for this document:",
            settings=settings,
            reply_markup={"inline_keyboard": rows},
        )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def _handle_set_cur(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Set currency on staging.extracted_data_json, then re-render review."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    try:
        staging_id = hex_to_uuid(params[0])
    except ValueError:
        answer_callback_query(callback_id=callback_id, text="Invalid ID.", settings=settings)
        return

    currency_code = params[1].upper()
    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    # Write currency into extracted JSON
    data = dict(staging.extracted_data_json or {})
    data["currency"] = currency_code
    staging.extracted_data_json = data
    db.commit()

    # Re-render review message
    supplier = None
    if staging.supplier_id:
        from sqlalchemy import select
        from app.db.models.suppliers import Supplier
        supplier = db.scalar(select(Supplier).where(Supplier.id == staging.supplier_id))

    doc_type = staging.document_type or "invoice"
    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text
    text = _build_review_text(staging, data, supplier, doc_type, page=0)
    keyboard = _build_review_keyboard(staging, data, sup_buttons=None, page=0)

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            settings=settings,
            reply_markup=keyboard,
        )
    answer_callback_query(
        callback_id=callback_id, text=f"Currency set: {currency_code}", settings=settings
    )


def _handle_rev_page(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Navigate to a different page of the review message."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="", settings=settings)
        return
    try:
        staging_id = hex_to_uuid(params[0])
        page = int(params[1])
    except (ValueError, IndexError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    supplier = None
    if staging.supplier_id:
        from sqlalchemy import select
        from app.db.models.suppliers import Supplier
        supplier = db.scalar(select(Supplier).where(Supplier.id == staging.supplier_id))

    extracted = staging.extracted_data_json or {}
    doc_type = staging.document_type or "invoice"

    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text
    text = _build_review_text(staging, extracted, supplier, doc_type, page=page)
    keyboard = _build_review_keyboard(staging, extracted, sup_buttons=None, page=page)

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            settings=settings,
            reply_markup=keyboard,
        )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def _handle_list_page(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Navigate paginated slash-command lists (products/prices/inventory/suppliers/team)."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    list_type = params[0]
    try:
        page = int(params[1])
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    if page < 0:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    raw_ctx = ctx_svc.get(user)
    ctx = raw_ctx if isinstance(raw_ctx, dict) else {}
    raw_active_id = ctx.get("active_list_message_id")
    active_message_id: int | None = None
    if isinstance(raw_active_id, int):
        active_message_id = raw_active_id
    elif isinstance(raw_active_id, str):
        try:
            active_message_id = int(raw_active_id)
        except ValueError:
            active_message_id = None

    if (
        active_message_id is not None
        and message_id is not None
        and message_id != active_message_id
    ):
        answer_callback_query(
            callback_id=callback_id,
            text="This list has expired. Run the command again.",
            show_alert=True,
            settings=settings,
        )
        return

    try:
        from app.telegram.handlers.commands import build_list_page

        text, reply_markup = build_list_page(
            list_type=list_type,
            page=page,
            user=user,
            db=db,
            ctx_svc=ctx_svc,
            settings=settings,
        )
    except ValueError as exc:
        answer_callback_query(
            callback_id=callback_id,
            text=str(exc),
            show_alert=True,
            settings=settings,
        )
        return

    db.commit()

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            settings=settings,
            reply_markup=reply_markup,
        )
    else:
        if reply_markup and reply_markup.get("inline_keyboard"):
            send_message_with_keyboard(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                settings=settings,
            )
        else:
            send_message(chat_id=chat_id, text=text, settings=settings)

    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def _edit_supplier_in_review(
    *,
    chat_id: int,
    message_id: int,
    staging: FileProcessingStaging,
    supplier,
    settings: Settings,
) -> None:
    """Re-render the full review message (page 0) after supplier is confirmed."""
    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text

    extracted = staging.extracted_data_json or {}
    doc_type = staging.document_type or "invoice"
    text = _build_review_text(staging, extracted, supplier, doc_type, page=0)
    keyboard = _build_review_keyboard(staging, extracted, sup_buttons=None, page=0)

    edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        settings=settings,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Supplier text-input handler
# ---------------------------------------------------------------------------


def _parse_message_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def handle_supplier_name_input(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Handle free-text supplier input after `type_sup` / `new_sup` prompt."""
    msg = update.get("message", {})
    text = (msg.get("text", "") or "").strip()
    chat_id = user.chat_id

    fields = ctx_svc.get_fields(user)
    staging_id_raw = fields.get("supplier_input_staging_id")
    mode = str(fields.get("supplier_input_mode") or "resolve")

    if not staging_id_raw:
        return
    if not text:
        send_message(chat_id=chat_id, text="Please type a supplier name.", settings=settings)
        return

    try:
        staging_id = _uuid_mod.UUID(str(staging_id_raw))
    except ValueError:
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
        db.commit()
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None or staging.status != "pending_review":
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
        db.commit()
        send_message(
            chat_id=chat_id,
            text="This upload is no longer awaiting review.",
            settings=settings,
        )
        return

    if not _is_authorized_for_staging(
        db=db,
        staging=staging,
        user=user,
        require_uploader_or_owner=True,
    ):
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
        db.commit()
        send_message(chat_id=chat_id, text=_AUTHZ_DENIED_TEXT, settings=settings)
        return

    extracted = dict(staging.extracted_data_json or {})
    extracted["supplier"] = text
    staging.extracted_data_json = extracted

    review_message_id = _parse_message_id(fields.get("review_message_id"))

    if mode == "create":
        svc = SupplierService(db)
        supplier = svc.create(
            name=text,
            user_id=user.id,
            restaurant_id=staging.restaurant_id,
        )
        staging.supplier_id = supplier.id
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
        db.commit()

        if review_message_id is not None:
            _edit_supplier_in_review(
                chat_id=chat_id,
                message_id=review_message_id,
                staging=staging,
                supplier=supplier,
                settings=settings,
            )
        send_message(
            chat_id=chat_id,
            text=f"✅ Supplier created: {supplier.name}",
            settings=settings,
        )
        return

    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text, _resolve_supplier

    supplier, sup_buttons = _resolve_supplier(
        name=text,
        restaurant_id=staging.restaurant_id,
        staging_id=staging.id,
        db=db,
    )
    if supplier is not None:
        staging.supplier_id = supplier.id
        ctx_svc.set_fields(
            user,
            supplier_input_staging_id=None,
            supplier_input_mode=None,
        )
        db.commit()

        if review_message_id is not None:
            _edit_supplier_in_review(
                chat_id=chat_id,
                message_id=review_message_id,
                staging=staging,
                supplier=supplier,
                settings=settings,
            )
        send_message(
            chat_id=chat_id,
            text=f"✅ Supplier set: {supplier.name}",
            settings=settings,
        )
        return

    ctx_svc.set_fields(
        user,
        supplier_input_staging_id=None,
        supplier_input_mode=None,
    )
    db.commit()

    review_text = _build_review_text(
        staging,
        extracted,
        None,
        staging.document_type or "invoice",
        page=0,
    )
    review_keyboard = _build_review_keyboard(
        staging,
        extracted,
        sup_buttons=sup_buttons,
        page=0,
    )

    if review_message_id is not None:
        edit_message_text(
            chat_id=chat_id,
            message_id=review_message_id,
            text=review_text,
            settings=settings,
            reply_markup=review_keyboard,
        )
    else:
        new_msg_id = send_message_with_keyboard(
            chat_id=chat_id,
            text=review_text,
            reply_markup=review_keyboard,
            settings=settings,
        )
        if new_msg_id:
            ctx_svc.set_fields(user, review_message_id=new_msg_id)
            db.commit()

    send_message(
        chat_id=chat_id,
        text=f'Got it. I looked up "{text}" - pick a supplier or create a new one.',
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Item edit handlers
# ---------------------------------------------------------------------------

_FIELD_LABELS = {
    "name":  "Name",
    "qty":   "Quantity",
    "unit":  "Unit",
    "price": "Unit price",
}


def _handle_ed_row(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Show a field-picker keyboard for editing a specific line item."""
    if len(params) < 2:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return
    try:
        staging_id = hex_to_uuid(params[0])
        item_idx = int(params[1])
    except (ValueError, IndexError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None or staging.status not in ("pending_review", "processing"):
        answer_callback_query(
            callback_id=callback_id, text="Upload not available for editing.", settings=settings
        )
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    items = (staging.extracted_data_json or {}).get("line_items", [])
    if item_idx >= len(items):
        answer_callback_query(callback_id=callback_id, text="Item not found.", settings=settings)
        return

    item = items[item_idx]
    doc_type = staging.document_type or "invoice"

    # Current values summary
    name = item.get("name", "?")
    unit = item.get("unit") or "—"
    price = item.get("unit_price")
    price_str = f"${price:.2f}" if price is not None else "—"
    summary_lines = [
        f"✏️ Editing item #{item_idx + 1}: {name}",
        f"Unit: {unit}   Price: {price_str}",
    ]
    if doc_type == "invoice":
        qty = item.get("qty")
        summary_lines.insert(1, f"Qty: {qty if qty is not None else '—'}   Unit: {unit}   Price: {price_str}")
        summary_lines.pop()  # remove duplicate unit/price line

    # Field buttons
    fields = ["name", "qty", "unit", "price"] if doc_type == "invoice" else ["name", "unit", "price"]
    field_buttons = [
        make_button(_FIELD_LABELS[f], cb_edit_field(staging_id, item_idx, f))
        for f in fields
    ]
    # Two per row
    rows = [field_buttons[i : i + 2] for i in range(0, len(field_buttons), 2)]
    rows.append([make_button("❌ Cancel", cb_confirm_upload(staging_id))])

    if message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\n".join(summary_lines) + "\n\nWhat do you want to change?",
            settings=settings,
            reply_markup={"inline_keyboard": rows},
        )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def _handle_ed_fld(*, params, user, db, ctx_svc, settings, callback_id, chat_id, message_id):
    """Store editing state in context and ask user to type the new value."""
    if len(params) < 3:
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return
    try:
        staging_id = hex_to_uuid(params[0])
        item_idx = int(params[1])
    except (ValueError, IndexError):
        answer_callback_query(callback_id=callback_id, text="Invalid.", settings=settings)
        return

    field = params[2]
    if field not in _FIELD_LABELS:
        answer_callback_query(callback_id=callback_id, text="Unknown field.", settings=settings)
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None:
        answer_callback_query(callback_id=callback_id, text="Upload not found.", settings=settings)
        return

    if not _authorize_staging_callback(
        db=db,
        staging=staging,
        user=user,
        callback_id=callback_id,
        settings=settings,
        require_uploader_or_owner=True,
    ):
        return

    # Store editing state in context
    ctx_svc.set_fields(
        user,
        editing_staging_id=str(staging_id),
        editing_item_idx=item_idx,
        editing_field=field,
    )
    db.commit()

    label = _FIELD_LABELS[field]
    hint = "a number (e.g. 3.5)" if field in ("qty", "price") else "text"
    send_message(
        chat_id=chat_id,
        text=f"Enter new {label} for item #{item_idx + 1} ({hint}):\n\nSend /cancel to abort.",
        settings=settings,
    )
    answer_callback_query(callback_id=callback_id, text="", settings=settings)


def handle_item_edit_input(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Handle free-text input when the user is in item-edit mode.

    Called from telegram_tasks._handle_llm when editing_staging_id is set in context.
    Updates line_items[idx][field] in extracted_data_json, clears editing state,
    re-renders the review message.
    """
    msg = update.get("message", {})
    text = (msg.get("text", "") or "").strip()
    chat_id = user.chat_id

    ctx_fields = ctx_svc.get_fields(user)
    staging_id_str = ctx_fields.get("editing_staging_id")
    item_idx = ctx_fields.get("editing_item_idx")
    field = ctx_fields.get("editing_field")

    if not staging_id_str or item_idx is None or not field:
        return  # stale state; ignore

    if not text:
        send_message(chat_id=chat_id, text="Please send a non-empty value.", settings=settings)
        return

    try:
        staging_id = _uuid_mod.UUID(staging_id_str)
    except ValueError:
        ctx_svc.set_fields(user, editing_staging_id=None, editing_item_idx=None, editing_field=None)
        db.commit()
        return

    staging = db.get(FileProcessingStaging, staging_id)
    if staging is None or staging.status not in ("pending_review", "processing"):
        ctx_svc.set_fields(user, editing_staging_id=None, editing_item_idx=None, editing_field=None)
        db.commit()
        send_message(chat_id=chat_id, text="Upload no longer available for editing.", settings=settings)
        return

    if not _is_authorized_for_staging(
        db=db,
        staging=staging,
        user=user,
        require_uploader_or_owner=True,
    ):
        ctx_svc.set_fields(user, editing_staging_id=None, editing_item_idx=None, editing_field=None)
        db.commit()
        send_message(chat_id=chat_id, text=_AUTHZ_DENIED_TEXT, settings=settings)
        return

    data = dict(staging.extracted_data_json or {})
    items = list(data.get("line_items", []))

    if item_idx >= len(items):
        ctx_svc.set_fields(user, editing_staging_id=None, editing_item_idx=None, editing_field=None)
        db.commit()
        send_message(chat_id=chat_id, text="Item no longer exists.", settings=settings)
        return

    item = dict(items[item_idx])

    # Parse and apply the new value
    json_key = "unit_price" if field == "price" else field
    if field in ("qty", "price"):
        try:
            value = float(text.replace(",", "").strip())
        except ValueError:
            send_message(
                chat_id=chat_id,
                text=f"Invalid number: {text!r}. Please enter a numeric value.",
                settings=settings,
            )
            return
        item[json_key] = value
    else:
        item[json_key] = text

    items[item_idx] = item
    data["line_items"] = items
    staging.extracted_data_json = data

    # Clear editing state
    ctx_svc.set_fields(user, editing_staging_id=None, editing_item_idx=None, editing_field=None)
    db.commit()

    # Re-render review message
    review_message_id = ctx_fields.get("review_message_id")
    supplier = None
    if staging.supplier_id:
        from sqlalchemy import select
        from app.db.models.suppliers import Supplier
        supplier = db.scalar(select(Supplier).where(Supplier.id == staging.supplier_id))

    doc_type = staging.document_type or "invoice"
    from app.workers.ocr_tasks import _build_review_keyboard, _build_review_text
    review_text = _build_review_text(staging, data, supplier, doc_type, page=0)
    review_keyboard = _build_review_keyboard(staging, data, sup_buttons=None, page=0)

    label = _FIELD_LABELS.get(field, field)
    send_message(chat_id=chat_id, text=f"✅ {label} updated.", settings=settings)

    if review_message_id:
        edit_message_text(
            chat_id=chat_id,
            message_id=review_message_id,
            text=review_text,
            settings=settings,
            reply_markup=review_keyboard,
        )
    else:
        # No tracked review message — send fresh one
        new_msg_id = send_message_with_keyboard(
            chat_id=chat_id,
            text=review_text,
            reply_markup=review_keyboard,
            settings=settings,
        )
        if new_msg_id:
            ctx_svc.set_fields(user, review_message_id=new_msg_id)
            db.commit()


# ---------------------------------------------------------------------------
# Quick stock adjustment button handlers
# ---------------------------------------------------------------------------


def _clear_adj_context(ctx_svc: ContextService, user: "User", db: "Session") -> None:
    ctx_svc.set_fields(user, adj_item_id=None, adj_item_name=None, adj_qty=None, adj_unit=None)
    db.commit()


def _handle_stock_conf(
    *,
    params: list[str],
    user: "User",
    db: "Session",
    ctx_svc: ContextService,
    settings: Settings,
    callback_id: str,
    chat_id: int,
    message_id: int | None,
    **_: object,
) -> None:
    """Confirm pending quick stock adjustment for a matched item."""
    if not params or params[0] not in ("in", "out"):
        answer_callback_query(callback_id=callback_id, text="Invalid action.", settings=settings)
        return

    direction = params[0]
    fields = ctx_svc.get_fields(user)
    item_id_raw = fields.get("adj_item_id")
    item_name = str(fields.get("adj_item_name") or "")
    qty_raw = fields.get("adj_qty")
    unit = fields.get("adj_unit") or None

    if not item_id_raw or not item_name or qty_raw is None:
        answer_callback_query(callback_id=callback_id, text="Expired — please type again.", settings=settings)
        if message_id:
            edit_message_text(chat_id=chat_id, message_id=message_id, text="Adjustment expired. Please type again.", settings=settings)
        return

    try:
        item_id = _uuid_mod.UUID(str(item_id_raw))
        qty = float(qty_raw)
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Expired — please type again.", settings=settings)
        return

    active_restaurant_id = ctx_svc.get_active_restaurant_id(user)
    if active_restaurant_id is None:
        answer_callback_query(callback_id=callback_id, text="No active restaurant.", settings=settings)
        return

    is_member, _ = _membership_flags(db=db, restaurant_id=active_restaurant_id, user_id=user.id)
    if not is_member:
        answer_callback_query(callback_id=callback_id, text=_AUTHZ_DENIED_TEXT, show_alert=True, settings=settings)
        _clear_adj_context(ctx_svc, user, db)
        return

    inv_svc = InventoryService(db)
    txn_type = "credit" if direction == "in" else "debit"
    inv_svc.record_transaction(
        restaurant_id=active_restaurant_id,
        item_id=item_id,
        txn_type=txn_type,
        quantity=qty,
        created_by=user.id,
        source="manual",
    )
    _clear_adj_context(ctx_svc, user, db)

    from app.db.models.inventory_balances import InventoryBalance
    from sqlalchemy import select as _sa_select
    new_balance = db.scalar(
        _sa_select(InventoryBalance.balance).where(
            InventoryBalance.item_id == item_id,
            InventoryBalance.restaurant_id == active_restaurant_id,
        )
    )
    bal_str = f"{float(new_balance):g}" if new_balance is not None else "?"
    unit_str = f" {unit}" if unit else ""
    verb = "Added" if direction == "in" else "Removed"
    result_text = f"✅ {verb} {qty:g}{unit_str} {item_name}. Balance: {bal_str}{unit_str}"

    answer_callback_query(callback_id=callback_id, text="", settings=settings)
    if message_id:
        edit_message_text(chat_id=chat_id, message_id=message_id, text=result_text, settings=settings)
    else:
        send_message(chat_id=chat_id, text=result_text, settings=settings)


def _handle_stock_new(
    *,
    params: list[str],
    user: "User",
    db: "Session",
    ctx_svc: ContextService,
    settings: Settings,
    callback_id: str,
    chat_id: int,
    message_id: int | None,
    **_: object,
) -> None:
    """Create new inventory item and record quick stock adjustment."""
    if not params or params[0] not in ("in", "out"):
        answer_callback_query(callback_id=callback_id, text="Invalid action.", settings=settings)
        return

    direction = params[0]
    fields = ctx_svc.get_fields(user)
    item_name = str(fields.get("adj_item_name") or "").strip()
    qty_raw = fields.get("adj_qty")
    unit = fields.get("adj_unit") or None

    if not item_name or qty_raw is None:
        answer_callback_query(callback_id=callback_id, text="Expired — please type again.", settings=settings)
        if message_id:
            edit_message_text(chat_id=chat_id, message_id=message_id, text="Adjustment expired. Please type again.", settings=settings)
        return

    try:
        qty = float(qty_raw)
    except (ValueError, TypeError):
        answer_callback_query(callback_id=callback_id, text="Expired — please type again.", settings=settings)
        return

    active_restaurant_id = ctx_svc.get_active_restaurant_id(user)
    if active_restaurant_id is None:
        answer_callback_query(callback_id=callback_id, text="No active restaurant.", settings=settings)
        return

    is_member, _ = _membership_flags(db=db, restaurant_id=active_restaurant_id, user_id=user.id)
    if not is_member:
        answer_callback_query(callback_id=callback_id, text=_AUTHZ_DENIED_TEXT, show_alert=True, settings=settings)
        _clear_adj_context(ctx_svc, user, db)
        return

    inv_svc = InventoryService(db)
    new_item, _ = inv_svc.get_or_create_item(
        restaurant_id=active_restaurant_id,
        name=item_name,
        unit=unit,
    )
    txn_type = "credit" if direction == "in" else "debit"
    inv_svc.record_transaction(
        restaurant_id=active_restaurant_id,
        item_id=new_item.id,
        txn_type=txn_type,
        quantity=qty,
        created_by=user.id,
        source="manual",
    )
    _clear_adj_context(ctx_svc, user, db)

    from app.db.models.inventory_balances import InventoryBalance
    from sqlalchemy import select as _sa_select
    new_balance = db.scalar(
        _sa_select(InventoryBalance.balance).where(
            InventoryBalance.item_id == new_item.id,
            InventoryBalance.restaurant_id == active_restaurant_id,
        )
    )
    bal_str = f"{float(new_balance):g}" if new_balance is not None else "?"
    unit_str = f" {unit}" if unit else ""
    verb = "Added" if direction == "in" else "Removed"
    result_text = f"✅ {verb} {qty:g}{unit_str} {new_item.name}. Balance: {bal_str}{unit_str}"

    answer_callback_query(callback_id=callback_id, text="", settings=settings)
    if message_id:
        edit_message_text(chat_id=chat_id, message_id=message_id, text=result_text, settings=settings)
    else:
        send_message(chat_id=chat_id, text=result_text, settings=settings)


def _handle_stock_cancel(
    *,
    user: "User",
    db: "Session",
    ctx_svc: ContextService,
    settings: Settings,
    callback_id: str,
    chat_id: int,
    message_id: int | None,
    **_: object,
) -> None:
    """Cancel pending quick stock adjustment."""
    _clear_adj_context(ctx_svc, user, db)
    answer_callback_query(callback_id=callback_id, text="", settings=settings)
    if message_id:
        edit_message_text(chat_id=chat_id, message_id=message_id, text="Cancelled.", settings=settings)
