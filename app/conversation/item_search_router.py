from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

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
    resolve_supplier_id_for_restaurant,
    search_supplier_items,
)
from app.db.models.restaurant_user import RestaurantUser
from sqlalchemy import select
from app.workers.telemetry import record_llm_call


_OPEN_RE = re.compile(r"^\s*(?:open|open\s+item)\s+(\d+)\s*$", flags=re.IGNORECASE)
_SUPPLIER_RE = re.compile(
    r"^\s*(?:supplier|supplier\s+details)\s+(\d+)\s*$", flags=re.IGNORECASE
)

_SEARCH_PREFIX_RE = re.compile(
    r"^\s*(?:search|find|lookup|look\s+up|show\s+me)\s+(?:item|items|product|products)?\s*",
    flags=re.IGNORECASE,
)


def _clean_semantic_query(text: str) -> str:
    cleaned = _SEARCH_PREFIX_RE.sub("", (text or "").strip()).strip()
    return cleaned or (text or "").strip()


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
        "query_plan": {
            "raw": raw,
            "queries": queries,
        },
        "restaurant_id": str(restaurant_id),
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
        if _session_expired(created_at=pending.get("created_at"), ttl_minutes=ttl_minutes):
            return (
                "Your last search expired. Please search again.",
                {"clear_pending_action": True},
            )

        mode = str(pending.get("mode") or "search")
        restaurant_id_s = pending.get("restaurant_id")
        try:
            restaurant_id = uuid.UUID(str(restaurant_id_s))
        except ValueError:
            return ("Your last search expired. Please search again.", {"clear_pending_action": True})

        has_access = db.scalar(
            select(RestaurantUser.id).where(
                RestaurantUser.restaurant_id == restaurant_id,
                RestaurantUser.user_id == user_id,
                RestaurantUser.status != "removed",
            )
        )
        if not has_access:
            return (responses.OUTLET_NO_ACCESS, {"clear_pending_action": True})

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
            if not pending.get("has_more", True):
                return ("No more results. Try a new search.", {"clear_pending_action": True})
            next_offset = offset + limit
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
                    "supplier_item_id": str(r.supplier_item_id),
                    "label": r.supplier_item_name,
                    "supplier_id": str(r.supplier_id),
                    "supplier_name": r.supplier_name,
                    "min_price": r.min_price,
                    "currency": r.currency,
                }
                for r in page.results
            ]
            response_text = format_item_search_list(mode=mode, page=page)
            return (
                response_text,
                {
                    "pending_action": _build_search_action(
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
                },
            )

        if m := _OPEN_RE.match(text):
            n = int(m.group(1))
            last_page = pending.get("last_page")
            if not isinstance(last_page, list) or not last_page:
                return ("No active search. Try searching first.", {"clear_pending_action": True})
            if n < 1 or n > len(last_page):
                return (
                    f"Pick a number between 1 and {len(last_page)}.",
                    {"pending_action": pending},
                )
            entry = last_page[n - 1]
            supplier_item_id_s = entry.get("supplier_item_id") if isinstance(entry, dict) else None
            try:
                supplier_item_id = uuid.UUID(str(supplier_item_id_s))
            except ValueError:
                return ("That item isn't available anymore. Try a new search.", {"clear_pending_action": True})
            details = load_item_details(
                db=db,
                restaurant_id=restaurant_id,
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
            last_page = pending.get("last_page")
            if not isinstance(last_page, list) or not last_page:
                return ("No active search. Try searching first.", {"clear_pending_action": True})
            if n < 1 or n > len(last_page):
                return (
                    f"Pick a number between 1 and {len(last_page)}.",
                    {"pending_action": pending},
                )
            entry = last_page[n - 1]
            supplier_id_s = entry.get("supplier_id") if isinstance(entry, dict) else None
            try:
                supplier_uuid = uuid.UUID(str(supplier_id_s))
            except ValueError:
                return ("That supplier isn't available anymore. Try a new search.", {"pending_action": pending})
            details = load_supplier_details(
                db=db,
                restaurant_id=restaurant_id,
                supplier_id=supplier_uuid,
                user_id=user_id,
            )
            if not details:
                return ("That supplier isn't available. Try a new search.", {"pending_action": pending})
            return (format_supplier_details(details=details), {"pending_action": pending})

        return None

    if isinstance(pending, dict) and pending.get("type") == "item_search_needs_outlet":
        queued = pending.get("queued_message_text")
        mode = str(pending.get("mode") or "search")
        if not isinstance(queued, str) or not queued.strip():
            return None

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

    semantic_text = _clean_semantic_query(overrides.semantic_text or message_text)
    plan, data, headers, latency_ms = plan_item_search(semantic_text=semantic_text, settings=settings)

    supplier_hint = overrides.supplier_hint or supplier_nlp or plan.supplier
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
        return response_text, {"clear_pending_action": True}

    last_page = [
        {
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
