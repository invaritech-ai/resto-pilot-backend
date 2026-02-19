"""Telegram update Celery task.

Fixes applied:
- update_id deduplication to prevent duplicate processing under concurrent/replayed scenarios
- Session-based transaction isolation for proper idempotency
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from app.core.config import get_settings
from app.services.context_service import ContextService
from app.services.dedup_service import DedupService
from app.services.restaurant_service import RestaurantService
from app.services.user_service import UserService
from app.telegram.handlers.onboarding import (
    handle as handle_onboarding,
    needs_onboarding,
)
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session
from app.workers.utils import _get_task_id

logger = logging.getLogger(__name__)


def _get_or_create_session(db, chat_id: int) -> uuid.UUID:
    """Get or create a telegram session for dedup tracking.

    We need a session_id for the telegram_messages table.
    Create a minimal session record if one doesn't exist.
    """
    from sqlalchemy import select
    from app.db.models.telegram_session import TelegramSessions

    # Try to find an existing open session for this chat
    existing = db.scalar(
        select(TelegramSessions.id)
        .where(
            TelegramSessions.chat_id == chat_id,
            TelegramSessions.status == "open",
        )
        .order_by(TelegramSessions.started_at.desc())
        .limit(1)
    )
    if existing:
        return existing

    # Create a new session
    session = TelegramSessions(
        chat_id=chat_id,
        started_at=dt.datetime.now(dt.timezone.utc),
        last_activity_at=dt.datetime.now(dt.timezone.utc),
        status="open",
    )
    db.add(session)
    db.flush()
    return session.id


@celery_app.task(name="handle_telegram_update")
def handle_telegram_update(update: dict) -> None:
    """Process a Telegram update: get-or-create user, onboard or route.

    Deduplication:
        - Uses update_id as idempotency key
        - Records update_id in telegram_messages before processing
        - If update_id already exists, skips processing entirely

    Transaction safety:
        - All state changes committed before sending user-facing messages
        - Onboarding handlers are responsible for their own commits
    """
    task_id = _get_task_id()
    settings = get_settings()

    update_id = update.get("update_id") if isinstance(update, dict) else None
    callback_query = (update.get("callback_query") or {}) if isinstance(update, dict) else {}
    message = (
        (update.get("message") or update.get("edited_message") or {})
        if isinstance(update, dict)
        else {}
    )

    if callback_query:
        # Button click: identity and context live inside callback_query, not top-level message.
        cq_from = callback_query.get("from") or {}
        cq_message = callback_query.get("message") or {}
        telegram_id: int | None = cq_from.get("id")
        username: str | None = cq_from.get("username")
        chat_id = (cq_message.get("chat") or {}).get("id") or telegram_id
        message_id: int | None = cq_message.get("message_id")
        text: str | None = callback_query.get("data")  # stored for audit, not routing
    else:
        from_data = (message.get("from") or {}) if isinstance(message, dict) else {}
        telegram_id = from_data.get("id") or (message.get("chat") or {}).get("id")
        username = from_data.get("username")
        chat_id = (message.get("chat") or {}).get("id") if isinstance(message, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        text = message.get("text") if isinstance(message, dict) else None

    logger.info(
        "celery_task_started name=handle_telegram_update task_id=%s update_id=%s chat_id=%s",
        task_id,
        update_id,
        chat_id,
    )

    if not telegram_id or not chat_id:
        logger.warning(
            "handle_telegram_update: missing telegram_id or chat_id, skipping"
        )
        return

    if update_id is None:
        logger.warning("handle_telegram_update: missing update_id, skipping")
        return

    if message_id is None:
        logger.warning("handle_telegram_update: missing message_id, skipping")
        return

    with worker_db_session() as db:
        # Reuse session_id from webhook ACK when available so downstream writes
        # (staging, outgoing messages, LLM calls) stay in one thread.
        session_id: uuid.UUID | None = None
        session_id_raw = update.get("_session_id") if isinstance(update, dict) else None
        if isinstance(session_id_raw, str):
            try:
                session_id = uuid.UUID(session_id_raw)
            except ValueError:
                logger.warning(
                    "handle_telegram_update: invalid _session_id=%s", session_id_raw
                )
        if session_id is None:
            session_id = _get_or_create_session(db, chat_id)

        from app.telegram.bot_api import (
            bind_current_session,
            bind_outgoing_db_logger,
            bind_outlet_badge,
        )
        from app.services.telemetry import record_outgoing_message

        def _outgoing_db_logger(
            out_chat_id: int,
            out_text: str,
            telegram_message_id: int | None,
        ) -> None:
            if not out_text.strip():
                return
            try:
                with worker_db_session() as telemetry_db:
                    record_outgoing_message(
                        db=telemetry_db,
                        session_id=session_id,
                        chat_id=out_chat_id,
                        kind="reply",
                        text=out_text,
                        telegram_message_id=telegram_message_id,
                    )
                    telemetry_db.commit()
            except Exception:
                logger.exception(
                    "telegram_outgoing_log_failed session_id=%s chat_id=%s",
                    session_id,
                    out_chat_id,
                )

        # Dedup check: try to record this update_id
        # This is the FIRST thing we do - before any other processing
        user_svc = UserService(db)
        user, created = user_svc.get_or_create(
            telegram_id=telegram_id,
            chat_id=chat_id,
            username=username,
        )

        dedup_svc = DedupService(db)
        if not dedup_svc.record_if_new(
            update_id=update_id,
            session_id=session_id,
            chat_id=chat_id,
            user_id=user.id,
            telegram_id=telegram_id,
            message_id=message_id,
            text=text,
        ):
            logger.info(
                "handle_telegram_update: duplicate update_id=%s, skipping", update_id
            )
            db.commit()  # Commit the session creation if any
            return

        if created:
            logger.info("new_user_created telegram_id=%s", telegram_id)

        user_svc.update_last_interaction(user)
        db.commit()  # Commit user creation + dedup record before onboarding

        outlet_badge_label: str | None = None
        try:
            from app.db.models.restaurant import Restaurant

            ctx_probe = ContextService(db)
            active_restaurant_id = ctx_probe.get_active_restaurant_id(user)
            if active_restaurant_id and RestaurantService(db).user_membership_exists(
                restaurant_id=active_restaurant_id,
                user_id=user.id,
            ):
                active_restaurant = db.get(Restaurant, active_restaurant_id)
                if active_restaurant is not None:
                    outlet_badge_label = active_restaurant.name
            if outlet_badge_label is None:
                memberships = RestaurantService(db).list_for_user(user_id=user.id)
                if len(memberships) == 1:
                    outlet_badge_label = memberships[0][0].name
        except Exception:
            logger.exception(
                "telegram_outlet_badge_resolve_failed user_id=%s",
                user.id,
            )
        if not outlet_badge_label:
            outlet_badge_label = "-"

        # Process the update. On any exception (including send_message failures),
        # delete the dedup record so Celery can retry and re-send the message.
        with (
            bind_current_session(session_id),
            bind_outgoing_db_logger(_outgoing_db_logger),
            bind_outlet_badge(outlet_badge_label),
        ):
            try:
                if needs_onboarding(user):
                    ctx_svc = ContextService(db)
                    handle_onboarding(update, user, db, ctx_svc, settings)
                else:
                    # Wire Router for onboarded users
                    from app.telegram.router import Handlers, Router, RouteContext
                    from app.telegram.handlers.reset import handle as handle_reset
                    from app.telegram.handlers.commands import handle as handle_command
                    from app.telegram.handlers.buttons import handle as handle_button
                    from app.telegram.bot_api import send_message

                    ctx_svc = ContextService(db)

                    def _make_reset_handler():
                        def handler(update: dict, ctx: RouteContext) -> None:
                            handle_reset(update, ctx.user, ctx.db, ctx_svc, settings)
                        return handler

                    def _make_button_handler():
                        def handler(update: dict, ctx: RouteContext) -> None:
                            handle_button(update, ctx.user, ctx.db, ctx_svc, settings)
                        return handler

                    def _make_command_handler():
                        def handler(update: dict, ctx: RouteContext) -> None:
                            handle_command(update, ctx.user, ctx.db, ctx_svc, settings)
                        return handler

                    def _handle_file(update: dict, ctx: RouteContext) -> None:
                        """Handle file/photo uploads — create staging + ask doc type."""
                        from app.telegram.handlers.files import handle as handle_files
                        handle_files(update, ctx.user, ctx.db, ctx_svc, settings)

                    def _handle_pattern(update: dict, ctx: RouteContext) -> None:
                        """Stub for pattern handler (#N, qty+unit)."""
                        msg = update.get("message", {})
                        text = msg.get("text", "")
                        logger.info("pattern_handler_stub: pattern=%s", text)
                        send_message(
                            chat_id=ctx.user.chat_id,
                            text=f"Pattern recognized: {text}",
                            settings=settings,
                        )

                    def _handle_llm(update: dict, ctx: RouteContext) -> None:
                        """LLM fallback — intercepts item-edit text input if editing context is active."""
                        # Check for active supplier-input flow before falling through.
                        edit_ctx = ctx_svc.get_fields(ctx.user)
                        if edit_ctx.get("supplier_input_staging_id") is not None:
                            from app.telegram.handlers.buttons import handle_supplier_name_input

                            handle_supplier_name_input(
                                update,
                                ctx.user,
                                ctx.db,
                                ctx_svc,
                                settings,
                            )
                            return

                        # Check for active item-edit flow before falling through to LLM stub
                        edit_ctx = ctx_svc.get_fields(ctx.user)
                        if (
                            edit_ctx.get("editing_staging_id") is not None
                            and edit_ctx.get("editing_item_idx") is not None
                            and edit_ctx.get("editing_field") is not None
                        ):
                            from app.telegram.handlers.buttons import handle_item_edit_input
                            handle_item_edit_input(update, ctx.user, ctx.db, ctx_svc, settings)
                            return

                        # Try natural-language quick stock adjustment
                        msg_text = (update.get("message", {}).get("text", "") or "").strip()
                        if msg_text:
                            from app.llm.item_parser import ParseError, parse_stock_adjustment
                            from app.services.inventory_service import InventoryService
                            try:
                                adj = parse_stock_adjustment(settings, msg_text)
                            except ParseError:
                                adj = None
                            if adj:
                                _dispatch_stock_adjustment(
                                    adj=adj,
                                    update=update,
                                    user=ctx.user,
                                    db=ctx.db,
                                    ctx_svc=ctx_svc,
                                    settings=settings,
                                )
                                return

                        logger.info("llm_handler_stub: falling back to LLM")
                        send_message(
                            chat_id=ctx.user.chat_id,
                            text="I'm not sure how to help with that. Try /help for available commands.",
                            settings=settings,
                        )

                    def _dispatch_stock_adjustment(
                        adj: dict,
                        update: dict,
                        user: "object",
                        db: "object",
                        ctx_svc: "object",
                        settings: "object",
                    ) -> None:
                        """Fuzzy-match adj item, store in context, show confirm keyboard."""
                        from app.services.inventory_service import InventoryService
                        from app.telegram.bot_api import send_message_with_keyboard
                        from app.telegram.keyboards import cb_stock_cancel, cb_stock_conf, cb_stock_new, make_button

                        active_restaurant_id = ctx_svc.get_active_restaurant_id(user)
                        if active_restaurant_id is None:
                            send_message(
                                chat_id=user.chat_id,
                                text="No active restaurant. Send /start to complete setup.",
                                settings=settings,
                            )
                            return

                        inv_svc = InventoryService(db)
                        item_name = adj["item_name"]
                        qty = adj["quantity"]
                        unit = adj.get("unit")
                        direction = adj.get("direction")  # "in", "out", or None

                        matches = inv_svc.fuzzy_match_item(active_restaurant_id, item_name, threshold=0.5)
                        unit_str = f" {unit}" if unit else ""

                        if matches:
                            best_item, best_score = matches[0]
                            # Store pending adjustment in context
                            ctx_svc.set_fields(
                                user,
                                adj_item_id=str(best_item.id),
                                adj_item_name=best_item.name,
                                adj_qty=qty,
                                adj_unit=unit,
                            )
                            db.commit()

                            display_name = best_item.name
                            display_unit = f" {best_item.unit}" if best_item.unit else unit_str

                            if best_score >= 0.8:
                                if direction:
                                    verb = "Add" if direction == "in" else "Remove"
                                    msg = f"📦 {verb} {qty:g}{display_unit} {display_name}?"
                                    buttons = [
                                        [make_button(f"✅ Yes", cb_stock_conf(direction)), make_button("❌ Cancel", cb_stock_cancel())],
                                    ]
                                else:
                                    msg = f"📦 {qty:g}{display_unit} {display_name} — add or remove?"
                                    buttons = [
                                        [make_button("➕ Add to stock", cb_stock_conf("in")), make_button("➖ Remove", cb_stock_conf("out"))],
                                        [make_button("❌ Cancel", cb_stock_cancel())],
                                    ]
                            else:
                                # Fuzzy suggestion
                                if direction:
                                    verb = "Add" if direction == "in" else "Remove"
                                    msg = f"📦 Did you mean '{display_name}'?\n{verb} {qty:g}{display_unit}?"
                                    buttons = [
                                        [make_button(f"✅ Yes, {display_name}", cb_stock_conf(direction)), make_button("➕ New item", cb_stock_new(direction))],
                                        [make_button("❌ Cancel", cb_stock_cancel())],
                                    ]
                                else:
                                    msg = f"📦 Did you mean '{display_name}'?\n{qty:g}{display_unit} — add or remove?"
                                    buttons = [
                                        [make_button("➕ Add", cb_stock_conf("in")), make_button("➖ Remove", cb_stock_conf("out"))],
                                        [make_button("➕ New item", cb_stock_new("in")), make_button("❌ Cancel", cb_stock_cancel())],
                                    ]
                        else:
                            # No match — offer to create new item
                            ctx_svc.set_fields(
                                user,
                                adj_item_id=None,
                                adj_item_name=item_name,
                                adj_qty=qty,
                                adj_unit=unit,
                            )
                            db.commit()

                            if direction:
                                verb = "Add" if direction == "in" else "Remove"
                                msg = f"📦 '{item_name}' not found.\n{verb} {qty:g}{unit_str} as a new item?"
                                buttons = [
                                    [make_button(f"➕ {verb} as new item", cb_stock_new(direction)), make_button("❌ Cancel", cb_stock_cancel())],
                                ]
                            else:
                                msg = f"📦 '{item_name}' not found. Add {qty:g}{unit_str} as a new item?"
                                buttons = [
                                    [make_button("➕ Add to stock", cb_stock_new("in")), make_button("➖ Remove from stock", cb_stock_new("out"))],
                                    [make_button("❌ Cancel", cb_stock_cancel())],
                                ]

                        send_message_with_keyboard(
                            chat_id=user.chat_id,
                            text=msg,
                            reply_markup={"inline_keyboard": buttons},
                            settings=settings,
                        )

                    handlers = Handlers(
                        reset=_make_reset_handler(),
                        button=_make_button_handler(),
                        command=_make_command_handler(),
                        file=_handle_file,
                        pattern=_handle_pattern,
                        llm=_handle_llm,
                    )

                    router = Router(handlers)
                    route_ctx = RouteContext(db=db, user=user)
                    router.route(update, route_ctx)

            except Exception:
                # Roll back any uncommitted handler state, then delete the dedup record
                # so Celery's retry can reprocess (re-send) this update.
                try:
                    db.rollback()
                    dedup_svc.delete_by_update_id(update_id)
                    db.commit()
                except Exception:
                    logger.exception(
                        "handle_telegram_update: failed to remove dedup for retry "
                        "update_id=%s",
                        update_id,
                    )
                raise
