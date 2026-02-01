from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.item_search_query_planner import plan_item_search
from app.ai.openrouter_generation import extract_openrouter_generation_id
from app.ai.openrouter_usage import extract_openrouter_usage
from app.conversation import responses
from app.conversation.context import UserContext
from app.core.config import Settings
from app.domain.services.item_search_service import (
    extract_nlp_hints,
    format_item_details,
    format_item_search_list,
    format_supplier_details,
    load_item_details,
    load_supplier_details,
    parse_item_search_tokens,
    resolve_restaurant_id,
    resolve_supplier_id_across_restaurants,
    resolve_supplier_id_for_restaurant,
    search_supplier_items,
    search_supplier_items_across_restaurants,
    user_restaurants,
)
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.supplier_items import SupplierItems
from app.db.models.suppliers import Suppliers
from app.workers.telemetry import record_llm_call

logger = logging.getLogger(__name__)


_OPEN_RE = re.compile(r"^\s*(?:open|open\s+item)\s+(\d+)\s*$", flags=re.IGNORECASE)
_SUPPLIER_RE = re.compile(
    r"^\s*(?:supplier|supplier\s+details)\s+(\d+)\s*$", flags=re.IGNORECASE
)

_SEARCH_PREFIX_RE = re.compile(
    r"^\s*(?:search|find|lookup|look\s+up|show\s+me)\s+(?:item|items|product|products)?\s*",
    flags=re.IGNORECASE,
)

_CLAUSE_FORMS = ("for", "from", "at", "in")


def _clean_semantic_query(text: str) -> str:
    cleaned = _SEARCH_PREFIX_RE.sub("", (text or "").strip()).strip()
    return cleaned or (text or "").strip()


def _strip_clause(*, text: str, keyword: str, value: str | None) -> str:
    if not value:
        return text
    words = [w for w in value.strip().split() if w]
    if not words:
        return text
    escaped_value = r"\s+".join(re.escape(w) for w in words)
    pattern = rf"\b{re.escape(keyword)}\b\s+{escaped_value}(?:\b|[.,!?]|$)"
    return re.sub(pattern, " ", text, flags=re.IGNORECASE).strip()


def _strip_outlet_supplier_phrases(*, text: str, outlet: str | None, supplier: str | None) -> str:
    cleaned = text
    cleaned = _strip_clause(text=cleaned, keyword="for", value=outlet)
    cleaned = _strip_clause(text=cleaned, keyword="at", value=outlet)
    cleaned = _strip_clause(text=cleaned, keyword="in", value=outlet)
    cleaned = _strip_clause(text=cleaned, keyword="from", value=supplier)
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned


def _is_more(text: str) -> bool:
    t = text.strip().lower().rstrip("!?.,")
    return t in {"more", "show more", "next"}


def _is_order_intent(text: str) -> bool:
    t = text.strip().lower()
    return t in {"order food", "place an order", "i want to order"} or (
        "order" in t and "food" in t
    )


def _is_new_search_intent(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    if t.startswith(("search ", "find ", "look up ", "lookup ")):
        deny = {"invoice", "invoices", "staff", "outlet", "outlets", "invite", "invites", "profile"}
        if any(word in t for word in deny):
            return False
        if ("supplier" in t or "suppliers" in t) and not any(word in t for word in {"item", "items", "product", "products"}):
            return False
        return True
    if t.startswith(("search item", "find item", "open item")):
        return True
    if t.startswith("show me "):
        deny = {"supplier", "suppliers", "invoice", "invoices", "staff", "outlet", "outlets", "invites", "profile"}
        if any(word in t for word in deny):
            return False
        return True
    if "search item" in t or "find item" in t:
        return True
    return False


def _session_expired(*, created_at: str | None, ttl_minutes: int) -> bool:
    if not created_at:
        return True
    try:
        ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.UTC)
    return dt.datetime.now(dt.UTC) - ts > dt.timedelta(minutes=max(1, ttl_minutes))


def _build_search_action(
    *,
    mode: str,
    raw: str,
    queries: list[str],
    restaurant_id: uuid.UUID,
    supplier_id: uuid.UUID | None,
    status: str,
    limit: int,
    offset: int,
    last_page: list[dict[str, Any]],
    has_more: bool,
) -> dict[str, Any]:
    return {
        "type": "item_search",
        "mode": mode,
        "scope": "one",
        "query_plan": {
            "raw": raw,
            "queries": queries,
        },
        "restaurant_id": str(restaurant_id),
        "restaurant_ids": [str(restaurant_id)],
        "supplier_id": str(supplier_id) if supplier_id else None,
        "status": status,
        "limit": limit,
        "offset": offset,
        "last_page": last_page,
        "has_more": has_more,
        "created_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    }


def _build_search_action_all(
    *,
    mode: str,
    raw: str,
    queries: list[str],
    restaurant_ids: list[uuid.UUID],
    supplier_id: uuid.UUID | None,
    status: str,
    limit: int,
    offset: int,
    last_page: list[dict[str, Any]],
    has_more: bool,
) -> dict[str, Any]:
    return {
        "type": "item_search",
        "mode": mode,
        "scope": "all",
        "query_plan": {"raw": raw, "queries": queries},
        "restaurant_ids": [str(r) for r in restaurant_ids],
        "supplier_id": str(supplier_id) if supplier_id else None,
        "status": status,
        "limit": limit,
        "offset": offset,
        "last_page": last_page,
        "has_more": has_more,
        "created_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    }


def try_handle_item_search_fast_path(
    *,
    db: Session,
    user_id: uuid.UUID,
    context: UserContext,
    message_text: str,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
) -> tuple[str, dict[str, Any]] | None:
    text = (message_text or "").strip()
    if not text:
        return None

    ttl_minutes = int(getattr(settings, "item_search_session_ttl_minutes", 30) or 30)

    pending = context.pending_action
    if isinstance(pending, dict) and pending.get("type") == "item_search":
        logger.info(
            "item_search_followup_received chat_id=%s user_id=%s",
            chat_id,
            str(user_id),
        )
        if _session_expired(created_at=pending.get("created_at"), ttl_minutes=ttl_minutes):
            return (
                "Your last search expired. Please search again.",
                {"clear_pending_action": True},
            )

        mode = str(pending.get("mode") or "search")
        scope = str(pending.get("scope") or "one")
        restaurant_ids: list[uuid.UUID] = []
        if scope == "all":
            raw_ids = pending.get("restaurant_ids")
            if isinstance(raw_ids, list):
                for rid_s in raw_ids:
                    try:
                        restaurant_ids.append(uuid.UUID(str(rid_s)))
                    except ValueError:
                        continue
        else:
            restaurant_id_s = pending.get("restaurant_id")
            try:
                restaurant_ids.append(uuid.UUID(str(restaurant_id_s)))
            except ValueError:
                return ("Your last search expired. Please search again.", {"clear_pending_action": True})

        if not restaurant_ids:
            return ("Your last search expired. Please search again.", {"clear_pending_action": True})

        allowed = {rid for rid, _name in user_restaurants(db, user_id=user_id)}
        if any(rid not in allowed for rid in restaurant_ids):
            return (responses.OUTLET_NO_ACCESS, {"clear_pending_action": True})

        restaurant_id = restaurant_ids[0]

        supplier_id: uuid.UUID | None = None
        if pending.get("supplier_id"):
            try:
                supplier_id = uuid.UUID(str(pending.get("supplier_id")))
            except ValueError:
                supplier_id = None

        status = str(pending.get("status") or "active")
        limit = int(pending.get("limit") or 5)
        offset = int(pending.get("offset") or 0)
        query_plan = pending.get("query_plan") if isinstance(pending.get("query_plan"), dict) else {}
        queries = query_plan.get("queries") if isinstance(query_plan.get("queries"), list) else []
        raw = str(query_plan.get("raw") or "").strip()

        if _is_more(text):
            logger.info(
                "item_search_followup_more chat_id=%s restaurant_id=%s mode=%s offset=%s limit=%s",
                chat_id,
                str(restaurant_id),
                mode,
                offset,
                limit,
            )
            if not pending.get("has_more", True):
                return ("No more results. Try a new search.", {"clear_pending_action": True})
            next_offset = offset + limit
            if scope == "all":
                page = search_supplier_items_across_restaurants(
                    db=db,
                    restaurant_ids=restaurant_ids,
                    queries=[str(q) for q in queries if isinstance(q, str)],
                    supplier_id=supplier_id,
                    status=status,
                    limit=limit,
                    offset=next_offset,
                    mode=mode,
                )
            else:
                page = search_supplier_items(
                    db=db,
                    restaurant_id=restaurant_id,
                    queries=[str(q) for q in queries if isinstance(q, str)],
                    supplier_id=supplier_id,
                    status=status,
                    limit=limit,
                    offset=next_offset,
                    mode=mode,
                )
            if not page.results:
                return ("No more results. Try a new search.", {"clear_pending_action": True})
            last_page = [
                {
                    "restaurant_id": str(r.restaurant_id),
                    "restaurant_name": r.restaurant_name,
                    "supplier_item_id": str(r.supplier_item_id),
                    "label": r.supplier_item_name,
                    "supplier_id": str(r.supplier_id),
                    "supplier_name": r.supplier_name,
                    "min_price": r.min_price,
                    "currency": r.currency,
                }
                for r in page.results
            ]
            response_text = format_item_search_list(
                mode=mode, page=page, include_outlet=True if scope == "all" else None
            )
            logger.info(
                "item_search_followup_more_results chat_id=%s results=%s has_more=%s next_offset=%s",
                chat_id,
                len(page.results),
                page.has_more,
                next_offset,
            )
            return (
                response_text,
                {
                    "pending_action": (
                        _build_search_action_all(
                            mode=mode,
                            raw=raw,
                            queries=[str(q) for q in queries if isinstance(q, str)],
                            restaurant_ids=restaurant_ids,
                            supplier_id=supplier_id,
                            status=status,
                            limit=limit,
                            offset=next_offset,
                            last_page=last_page,
                            has_more=page.has_more,
                        )
                        if scope == "all"
                        else _build_search_action(
                            mode=mode,
                            raw=raw,
                            queries=[str(q) for q in queries if isinstance(q, str)],
                            restaurant_id=restaurant_id,
                            supplier_id=supplier_id,
                            status=status,
                            limit=limit,
                            offset=next_offset,
                            last_page=last_page,
                            has_more=page.has_more,
                        )
                    )
                },
            )

        if m := _OPEN_RE.match(text):
            n = int(m.group(1))
            logger.info(
                "item_search_followup_open chat_id=%s restaurant_id=%s mode=%s index=%s",
                chat_id,
                str(restaurant_id),
                mode,
                n,
            )
            last_page = pending.get("last_page")
            if not isinstance(last_page, list) or not last_page:
                return ("No active search. Try searching first.", {"clear_pending_action": True})
            if n < 1 or n > len(last_page):
                return (
                    f"Pick a number between 1 and {len(last_page)}.",
                    {"pending_action": pending},
                )
            entry = last_page[n - 1]
            entry_restaurant_id = restaurant_id
            if isinstance(entry, dict) and entry.get("restaurant_id"):
                try:
                    entry_restaurant_id = uuid.UUID(str(entry.get("restaurant_id")))
                except ValueError:
                    entry_restaurant_id = restaurant_id
            supplier_item_id_s = entry.get("supplier_item_id") if isinstance(entry, dict) else None
            try:
                supplier_item_id = uuid.UUID(str(supplier_item_id_s))
            except ValueError:
                return ("That item isn't available anymore. Try a new search.", {"clear_pending_action": True})
            details = load_item_details(
                db=db,
                restaurant_id=entry_restaurant_id,
                supplier_item_id=supplier_item_id,
                user_id=user_id,
            )
            if not details:
                return ("That item isn't available. Try a new search.", {"clear_pending_action": True})
            return (format_item_details(details=details), {"pending_action": pending})

        if m := _SUPPLIER_RE.match(text):
            if mode != "order":
                return ('Use "open <n>" to view details.', {"pending_action": pending})
            n = int(m.group(1))
            logger.info(
                "item_search_followup_supplier chat_id=%s restaurant_id=%s index=%s",
                chat_id,
                str(restaurant_id),
                n,
            )
            last_page = pending.get("last_page")
            if not isinstance(last_page, list) or not last_page:
                return ("No active search. Try searching first.", {"clear_pending_action": True})
            if n < 1 or n > len(last_page):
                return (
                    f"Pick a number between 1 and {len(last_page)}.",
                    {"pending_action": pending},
                )
            entry = last_page[n - 1]
            entry_restaurant_id = restaurant_id
            if isinstance(entry, dict) and entry.get("restaurant_id"):
                try:
                    entry_restaurant_id = uuid.UUID(str(entry.get("restaurant_id")))
                except ValueError:
                    entry_restaurant_id = restaurant_id
            supplier_id_s = entry.get("supplier_id") if isinstance(entry, dict) else None
            try:
                supplier_uuid = uuid.UUID(str(supplier_id_s))
            except ValueError:
                return ("That supplier isn't available anymore. Try a new search.", {"pending_action": pending})
            details = load_supplier_details(
                db=db,
                restaurant_id=entry_restaurant_id,
                supplier_id=supplier_uuid,
                user_id=user_id,
            )
            if not details:
                return ("That supplier isn't available. Try a new search.", {"pending_action": pending})
            return (format_supplier_details(details=details), {"pending_action": pending})

        # Not a follow-up command (more/open/supplier). Treat it as a new message (e.g. a new search).

    if isinstance(pending, dict) and pending.get("type") == "item_search_needs_outlet":
        queued = pending.get("queued_message_text")
        mode = str(pending.get("mode") or "search")
        if not isinstance(queued, str) or not queued.strip():
            return None

        logger.info(
            "item_search_needs_outlet_reply chat_id=%s user_id=%s text=%r mode=%s",
            chat_id,
            str(user_id),
            text,
            mode,
        )
        rid, restaurants = resolve_restaurant_id(
            db=db,
            user_id=user_id,
            hint=text,
            active_restaurant_id=None,
        )
        if rid is None:
            options = [{"id": str(r), "name": name, "role": "staff"} for r, name in restaurants]
            return (responses.outlet_select_prompt(options), {"pending_action": pending})

        ctx_update: dict[str, Any] = {"active_restaurant_id": str(rid)}
        response_text, search_update = _run_new_search(
            db=db,
            user_id=user_id,
            context=context,
            message_text=queued,
            mode=mode,
            restaurant_id=rid,
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
        )
        ctx_update.update(search_update)
        return response_text, ctx_update

    if isinstance(pending, dict) and pending.get("type") == "order_food_needs_outlet":
        logger.info(
            "order_food_needs_outlet_reply chat_id=%s user_id=%s text=%r",
            chat_id,
            str(user_id),
            text,
        )
        rid, restaurants = resolve_restaurant_id(
            db=db,
            user_id=user_id,
            hint=text,
            active_restaurant_id=None,
        )
        if rid is None:
            options = [{"id": str(r), "name": name, "role": "staff"} for r, name in restaurants]
            return (responses.outlet_select_prompt(options), {"pending_action": pending})
        return (
            "What would you like to order?",
            {
                "active_restaurant_id": str(rid),
                "pending_action": {"type": "order_food_prompt", "restaurant_id": str(rid)},
            },
        )

    if isinstance(pending, dict) and pending.get("type") == "order_food_prompt":
        logger.info("order_food_prompt_reply chat_id=%s user_id=%s", chat_id, str(user_id))
        rid_s = pending.get("restaurant_id")
        rid: uuid.UUID | None = None
        if rid_s:
            try:
                rid = uuid.UUID(str(rid_s))
            except ValueError:
                rid = None
        if rid is None:
            rid, restaurants = resolve_restaurant_id(
                db=db,
                user_id=user_id,
                hint=None,
                active_restaurant_id=context.active_restaurant_id,
            )
            if rid is None:
                options = [{"id": str(r), "name": name, "role": "staff"} for r, name in restaurants]
                return (
                    responses.outlet_select_prompt(options),
                    {"pending_action": {"type": "item_search_needs_outlet", "mode": "order", "queued_message_text": text}},
                )

        response_text, search_update = _run_new_search(
            db=db,
            user_id=user_id,
            context=context,
            message_text=text,
            mode="order",
            restaurant_id=rid,
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
        )
        return response_text, {"active_restaurant_id": str(rid), **search_update}

    if _is_order_intent(text):
        outlet_nlp, _supplier_nlp = extract_nlp_hints(text)
        logger.info(
            "order_food_intent chat_id=%s user_id=%s outlet_hint=%r",
            chat_id,
            str(user_id),
            outlet_nlp,
        )
        rid, restaurants = resolve_restaurant_id(
            db=db,
            user_id=user_id,
            hint=outlet_nlp,
            active_restaurant_id=context.active_restaurant_id,
        )
        if rid is None:
            options = [{"id": str(r), "name": name, "role": "staff"} for r, name in restaurants]
            return (
                responses.outlet_select_prompt(options),
                {"pending_action": {"type": "order_food_needs_outlet"}},
            )
        return (
            "What would you like to order?",
            {
                "active_restaurant_id": str(rid),
                "pending_action": {"type": "order_food_prompt", "restaurant_id": str(rid)},
            },
        )

    if not _is_new_search_intent(text):
        return None

    overrides = parse_item_search_tokens(text)
    outlet_nlp, supplier_nlp = extract_nlp_hints(text)

    outlet_hint = overrides.outlet_hint or outlet_nlp
    logger.info(
        "item_search_new chat_id=%s user_id=%s outlet_hint=%r supplier_hint=%r",
        chat_id,
        str(user_id),
        outlet_hint,
        overrides.supplier_hint or supplier_nlp,
    )
    # If no outlet is mentioned, search across all outlets the user can access.
    if not outlet_hint:
        response_text, search_update = _run_new_search_all(
            db=db,
            user_id=user_id,
            context=context,
            message_text=text,
            mode="search",
            settings=settings,
            session_id=session_id,
            chat_id=chat_id,
        )
        return response_text, search_update

    rid, restaurants = resolve_restaurant_id(
        db=db,
        user_id=user_id,
        hint=outlet_hint,
        active_restaurant_id=context.active_restaurant_id,
    )
    if rid is None:
        options = [{"id": str(r), "name": name, "role": "staff"} for r, name in restaurants]
        return (
            responses.outlet_select_prompt(options),
            {"pending_action": {"type": "item_search_needs_outlet", "mode": "search", "queued_message_text": text}},
        )

    response_text, search_update = _run_new_search(
        db=db,
        user_id=user_id,
        context=context,
        message_text=text,
        mode="search",
        restaurant_id=rid,
        settings=settings,
        session_id=session_id,
        chat_id=chat_id,
    )
    if search_update:
        pending_action = search_update.get("pending_action") if isinstance(search_update, dict) else None
        if isinstance(pending_action, dict):
            last_page = pending_action.get("last_page")
            logger.info(
                "item_search_new_results chat_id=%s restaurant_id=%s results=%s has_more=%s",
                chat_id,
                str(rid),
                len(last_page) if isinstance(last_page, list) else None,
                pending_action.get("has_more"),
            )
    return response_text, {"active_restaurant_id": str(rid), **search_update}


def _run_new_search(
    *,
    db: Session,
    user_id: uuid.UUID,
    context: UserContext,
    message_text: str,
    mode: str,
    restaurant_id: uuid.UUID,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
) -> tuple[str, dict[str, Any]]:
    overrides = parse_item_search_tokens(message_text)
    outlet_nlp, supplier_nlp = extract_nlp_hints(message_text)

    outlet_hint = overrides.outlet_hint or outlet_nlp
    supplier_hint = overrides.supplier_hint or supplier_nlp

    semantic_text = _clean_semantic_query(overrides.semantic_text or message_text)
    semantic_text = _strip_outlet_supplier_phrases(
        text=semantic_text,
        outlet=outlet_hint,
        supplier=supplier_hint,
    )
    plan, data, headers, latency_ms = plan_item_search(semantic_text=semantic_text, settings=settings)

    supplier_hint = supplier_hint or plan.supplier
    status = overrides.status or plan.status or "active"
    limit = overrides.limit or plan.limit or 5
    limit = max(1, min(10, int(limit)))

    supplier_id = resolve_supplier_id_for_restaurant(
        db=db,
        restaurant_id=restaurant_id,
        supplier_hint=supplier_hint,
    )
    if supplier_hint and supplier_id is None:
        return (
            f"I couldn't find a supplier named '{supplier_hint}' for this outlet. Try another supplier name, or search without it.",
            {},
        )

    # Telemetry for the parse step (even if it falls back)
    if data is not None:
        usage = extract_openrouter_usage(data)
        generation_id = extract_openrouter_generation_id(headers=headers, data=data)
        model_used = data.get("model", settings.openai_model) if isinstance(data, dict) else settings.openai_model
        record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="item_search_parse",
            model=model_used if isinstance(model_used, str) else settings.openai_model,
            openrouter_generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
            latency_ms=latency_ms,
        )

    page = search_supplier_items(
        db=db,
        restaurant_id=restaurant_id,
        queries=plan.queries,
        supplier_id=supplier_id,
        status=status,
        limit=limit,
        offset=0,
        mode=mode,
    )
    response_text = format_item_search_list(mode=mode, page=page)
    if not page.results:
        # Lightweight, best-effort diagnostic: count items linked to this outlet via restaurant_suppliers.
        conditions = [
            RestaurantSuppliers.restaurant_id == restaurant_id,
            RestaurantSuppliers.status == "active",
            Suppliers.is_active == True,
        ]
        if status == "active":
            conditions.append(SupplierItems.status == "active")
        elif status == "inactive":
            conditions.append(SupplierItems.status != "active")
        item_count = db.scalar(
            select(func.count())
            .select_from(SupplierItems)
            .join(Suppliers, Suppliers.id == SupplierItems.supplier_id)
            .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
            .where(*conditions)
        )
        item_count = int(item_count or 0)
        q = (plan.raw or "").strip()
        if item_count == 0:
            return (
                f"{response_text}\n\nNo supplier items are loaded for this outlet yet. Upload a price list or invoice and /confirm.",
                {"clear_pending_action": True},
            )
        if q:
            return (
                f'{response_text}\n\nSearched {item_count} items, but none matched "{q}".',
                {"clear_pending_action": True},
            )
        return (f"{response_text}\n\nSearched {item_count} items.", {"clear_pending_action": True})

    last_page = [
        {
            "restaurant_id": str(r.restaurant_id),
            "restaurant_name": r.restaurant_name,
            "supplier_item_id": str(r.supplier_item_id),
            "label": r.supplier_item_name,
            "supplier_id": str(r.supplier_id),
            "supplier_name": r.supplier_name,
            "min_price": r.min_price,
            "currency": r.currency,
        }
        for r in page.results
    ]

    pending_action = _build_search_action(
        mode=mode,
        raw=plan.raw,
        queries=plan.queries,
        restaurant_id=restaurant_id,
        supplier_id=supplier_id,
        status=status,
        limit=limit,
        offset=0,
        last_page=last_page,
        has_more=page.has_more,
    )
    return response_text, {"pending_action": pending_action}


def _run_new_search_all(
    *,
    db: Session,
    user_id: uuid.UUID,
    context: UserContext,
    message_text: str,
    mode: str,
    settings: Settings,
    session_id: uuid.UUID,
    chat_id: int,
) -> tuple[str, dict[str, Any]]:
    overrides = parse_item_search_tokens(message_text)
    _outlet_nlp, supplier_nlp = extract_nlp_hints(message_text)

    supplier_hint = overrides.supplier_hint or supplier_nlp
    semantic_text = _clean_semantic_query(overrides.semantic_text or message_text)
    semantic_text = _strip_outlet_supplier_phrases(text=semantic_text, outlet=None, supplier=supplier_hint)
    plan, data, headers, latency_ms = plan_item_search(semantic_text=semantic_text, settings=settings)

    supplier_hint = supplier_hint or plan.supplier
    status = overrides.status or plan.status or "active"
    limit = overrides.limit or plan.limit or 5
    limit = max(1, min(10, int(limit)))

    restaurants = user_restaurants(db, user_id=user_id)
    if not restaurants:
        return (responses.ERROR_NO_OUTLETS, {"clear_pending_action": True})
    restaurant_ids = [rid for rid, _name in restaurants]

    supplier_id = resolve_supplier_id_across_restaurants(
        db=db,
        restaurant_ids=restaurant_ids,
        supplier_hint=supplier_hint,
    )
    if supplier_hint and supplier_id is None:
        return (
            f"I couldn't find a supplier named '{supplier_hint}' across your outlets. Try a different supplier name, or include an outlet.",
            {},
        )

    if data is not None:
        usage = extract_openrouter_usage(data)
        generation_id = extract_openrouter_generation_id(headers=headers, data=data)
        model_used = data.get("model", settings.openai_model) if isinstance(data, dict) else settings.openai_model
        record_llm_call(
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            purpose="item_search_parse",
            model=model_used if isinstance(model_used, str) else settings.openai_model,
            openrouter_generation_id=generation_id,
            usage=usage if isinstance(usage, dict) else {},
            latency_ms=latency_ms,
        )

    page = search_supplier_items_across_restaurants(
        db=db,
        restaurant_ids=restaurant_ids,
        queries=plan.queries,
        supplier_id=supplier_id,
        status=status,
        limit=limit,
        offset=0,
        mode=mode,
    )
    response_text = format_item_search_list(mode=mode, page=page, include_outlet=True)
    if not page.results:
        conditions = [
            RestaurantSuppliers.restaurant_id.in_(restaurant_ids),
            RestaurantSuppliers.status == "active",
            Suppliers.is_active == True,
        ]
        if status == "active":
            conditions.append(SupplierItems.status == "active")
        elif status == "inactive":
            conditions.append(SupplierItems.status != "active")
        item_count = db.scalar(
            select(func.count())
            .select_from(SupplierItems)
            .join(Suppliers, Suppliers.id == SupplierItems.supplier_id)
            .join(RestaurantSuppliers, RestaurantSuppliers.supplier_id == Suppliers.id)
            .where(*conditions)
        )
        item_count = int(item_count or 0)
        q = (plan.raw or "").strip()
        outlets_n = len(restaurant_ids)
        if item_count == 0:
            return (
                f"{response_text}\n\nNo supplier items are loaded across your {outlets_n} outlets yet. Upload a price list or invoice and /confirm.",
                {"clear_pending_action": True},
            )
        if q:
            return (
                f'{response_text}\n\nSearched {item_count} items across {outlets_n} outlets, but none matched "{q}".',
                {"clear_pending_action": True},
            )
        return (
            f"{response_text}\n\nSearched {item_count} items across {outlets_n} outlets.",
            {"clear_pending_action": True},
        )

    last_page = [
        {
            "restaurant_id": str(r.restaurant_id),
            "restaurant_name": r.restaurant_name,
            "supplier_item_id": str(r.supplier_item_id),
            "label": r.supplier_item_name,
            "supplier_id": str(r.supplier_id),
            "supplier_name": r.supplier_name,
            "min_price": r.min_price,
            "currency": r.currency,
        }
        for r in page.results
    ]

    pending_action = _build_search_action_all(
        mode=mode,
        raw=plan.raw,
        queries=plan.queries,
        restaurant_ids=restaurant_ids,
        supplier_id=supplier_id,
        status=status,
        limit=limit,
        offset=0,
        last_page=last_page,
        has_more=page.has_more,
    )
    return response_text, {"pending_action": pending_action}
