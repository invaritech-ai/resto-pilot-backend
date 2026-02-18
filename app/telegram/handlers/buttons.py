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

  use_match:{staging_hex}:{idx}:{item_hex}
      Accept fuzzy match for item[idx] → update resolution hub

  mk_item:{staging_hex}:{idx}
      Create new inventory item for item[idx] → update resolution hub

  skip_item:{staging_hex}:{idx}
      Skip item[idx] (not added to inventory) → update resolution hub

Stubs (not yet implemented):
  ed_row, list_p, res_h
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.inventory_service import InventoryService
from app.services.price_service import PriceService
from app.services.restaurant_service import RestaurantService
from app.services.staging_service import StagingService
from app.services.supplier_service import SupplierService
from app.telegram.bot_api import answer_callback_query, edit_message_text, send_message, send_message_with_keyboard
from app.telegram.keyboards import (
    cb_delete_upload,
    cb_mk_item,
    cb_open_upload,
    cb_pick_currency,
    cb_set_currency,
    cb_skip_item,
    cb_use_match,
    hex_to_uuid,
    make_button,
)

logger = logging.getLogger(__name__)

_MAX_HUB_ITEMS = 10  # show at most 10 pending items in resolution hub


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
        "doc_type":  _handle_doc_type,
        "conf_u":    _handle_conf_u,
        "del_u":     _handle_del_u,
        "set_sup":   _handle_set_sup,
        "new_sup":   _handle_new_sup,
        "use_match": _handle_use_match,
        "mk_item":   _handle_mk_item,
        "skip_item": _handle_skip_item,
        "rev_p":     _handle_rev_page,
        "open_u":    _handle_open_u,
        "pick_cur":  _handle_pick_cur,
        "set_cur":   _handle_set_cur,
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
    elif action == "ed_row":
        answer_callback_query(
            callback_id=callback_id, text="Item editing coming soon.", settings=settings
        )
    elif action in ("list_p", "res_h"):
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

    process_file_task.delay(staging_hex, doc_type, chat_id)

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

    # Auth check
    is_owner = staging.uploaded_by == user.id
    is_member = RestaurantService(db).user_membership_exists(
        restaurant_id=staging.restaurant_id, user_id=user.id
    )
    if not (is_owner or is_member):
        answer_callback_query(callback_id=callback_id, text="Not authorised.", settings=settings)
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
                text=f"✅ Price list confirmed — {count} price{'s' if count != 1 else ''} saved.",
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
    resolutions: dict = ctx.get("pending_item_resolutions") or {}

    inv_svc = InventoryService(db)

    if not resolutions:
        # First time: compute fuzzy matches for all items
        for i, li in enumerate(items):
            name = li.get("name", "")
            matches = inv_svc.fuzzy_match_item(
                restaurant_id=staging.restaurant_id, name=name, threshold=0.5
            )
            if matches:
                best, score = matches[0]
                if score >= 0.8:
                    resolutions[str(i)] = str(best.id)  # auto-resolved
                else:
                    resolutions[str(i)] = None  # needs user input
            else:
                resolutions[str(i)] = None  # no match

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
        if not supplier_name:
            answer_callback_query(
                callback_id=callback_id,
                text="Supplier options expired. Please retry.",
                show_alert=True,
                settings=settings,
            )
            return

        svc = SupplierService(db)
        matches = svc.fuzzy_search_for_restaurant(
            name=supplier_name,
            restaurant_id=staging.restaurant_id,
            threshold=0.5,
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
        except Exception:
            pass  # already linked or error — proceed anyway

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

    # Get supplier name from extracted data
    extracted = staging.extracted_data_json or {}
    supplier_name = (extracted.get("supplier") or "").strip()
    if not supplier_name:
        supplier_name = "New Supplier"

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
    # Build name → item_id map
    final: dict[str, _uuid_mod.UUID | None] = {}
    for i, li in enumerate(items):
        name = li.get("name", "")
        res = resolutions.get(str(i))
        if res is None or res == "skip":
            final[name] = None
        elif res == "new":
            new_item, _ = inv_svc.get_or_create_item(
                restaurant_id=staging.restaurant_id,
                name=name,
                unit=li.get("unit"),
            )
            final[name] = new_item.id
        else:
            try:
                final[name] = _uuid_mod.UUID(res)
            except ValueError:
                final[name] = None

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
