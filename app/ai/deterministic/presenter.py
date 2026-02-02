from __future__ import annotations

import json
from typing import Any

from app.conversation import responses


def _format_error(data: dict[str, Any]) -> str:
    msg = data.get("error")
    msg_s = msg.strip() if isinstance(msg, str) else "Error."
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        lines = [msg_s, ""]
        for i, c in enumerate(choices, 1):
            if isinstance(c, str) and c.strip():
                lines.append(f"{i}) {c.strip()}")
        return "\n".join(lines).strip()
    return msg_s


def present_tool_result(*, tool: str, tool_response_json: str) -> str:
    """
    Deterministically present tool JSON as user-facing text.

    This avoids extra LLM calls (and provider flakiness) in deterministic execution mode.
    """
    try:
        data = json.loads(tool_response_json)
    except Exception:
        return tool_response_json

    if not isinstance(data, dict):
        return tool_response_json

    if "error" in data:
        return _format_error(data)

    if tool == "help":
        text = data.get("text")
        return text if isinstance(text, str) and text.strip() else responses.MAIN_MENU

    if tool == "profile_get":
        profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
        return responses.profile_view(
            profile.get("full_name") if isinstance(profile.get("full_name"), str) else None,
            profile.get("phone") if isinstance(profile.get("phone"), str) else None,
            profile.get("username") if isinstance(profile.get("username"), str) else None,
        )

    if tool == "profile_update":
        profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
        parts: list[str] = []
        if "full_name" in profile:
            parts.append(f"Name: {profile.get('full_name') or 'cleared'}")
        if "phone" in profile:
            parts.append(f"Phone: {profile.get('phone') or 'cleared'}")
        if "username" in profile:
            uname = profile.get("username")
            parts.append(f"Username: @{uname}" if isinstance(uname, str) and uname else "Username: cleared")
        return "Updated your profile.\n" + "\n".join(parts) if parts else "Updated your profile."

    if tool == "restaurants_list":
        restaurants = data.get("restaurants") if isinstance(data.get("restaurants"), list) else []
        mapped = []
        for r in restaurants:
            if not isinstance(r, dict):
                continue
            name = r.get("name")
            role = r.get("your_role")
            if isinstance(name, str) and name.strip():
                mapped.append({"name": name.strip(), "role": role if isinstance(role, str) else None})
        return responses.outlets_list(mapped)

    if tool == "restaurants_select":
        name = data.get("restaurant_name")
        if isinstance(name, str) and name.strip():
            return f"{name.strip()} outlet selected."
        return "Outlet selected."

    if tool == "restaurants_create":
        restaurant = data.get("restaurant") if isinstance(data.get("restaurant"), dict) else {}
        name = restaurant.get("name")
        if isinstance(name, str) and name.strip():
            return responses.OUTLET_CREATED.format(name=name.strip())
        return "Created outlet."

    if tool == "restaurants_update":
        restaurant = data.get("restaurant") if isinstance(data.get("restaurant"), dict) else {}
        name = restaurant.get("name")
        if isinstance(name, str) and name.strip():
            return responses.OUTLET_UPDATED.format(name=name.strip())
        return "Updated outlet."

    if tool == "staff_list":
        members = data.get("members") if isinstance(data.get("members"), list) else []
        restaurant_name = data.get("restaurant_name")
        mapped = []
        for m in members:
            if not isinstance(m, dict):
                continue
            name = m.get("name")
            role = m.get("role")
            username = m.get("username")
            if isinstance(username, str) and username.startswith("@"):
                username = username[1:]
            mapped.append(
                {
                    "name": name if isinstance(name, str) else None,
                    "role": role if isinstance(role, str) else None,
                    "username": username if isinstance(username, str) else None,
                }
            )
        return responses.staff_list(mapped, outlet_name=restaurant_name if isinstance(restaurant_name, str) else None)

    if tool == "suppliers_list":
        suppliers = data.get("suppliers") if isinstance(data.get("suppliers"), list) else []
        mapped = []
        for s in suppliers:
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip():
                mapped.append({"name": s["name"].strip()})
            elif isinstance(s, str) and s.strip():
                mapped.append({"name": s.strip()})
        return responses.suppliers_list(mapped)

    if tool in {"restaurant_suppliers_link", "restaurant_suppliers_unlink"}:
        restaurant_name = data.get("restaurant_name")
        supplier_name = data.get("supplier_name")
        if isinstance(restaurant_name, str) and isinstance(supplier_name, str):
            verb = "Linked" if tool.endswith("_link") else "Unlinked"
            return f"{verb} {supplier_name} {('to' if verb == 'Linked' else 'from')} {restaurant_name}."
        return "Done."

    if tool == "invite_codes_create":
        deep_link = data.get("deep_link")
        restaurant_name = data.get("restaurant_name")
        role = data.get("role")
        if isinstance(deep_link, str) and deep_link.strip():
            role_s = role if isinstance(role, str) and role else "staff"
            rest = restaurant_name if isinstance(restaurant_name, str) and restaurant_name else "the outlet"
            return responses.staff_invite_created(deep_link=deep_link.strip(), role=role_s, restaurant_name=rest)
        return "Invite created."

    if tool == "invite_codes_list":
        invites = data.get("invites") if isinstance(data.get("invites"), list) else []
        restaurant_name = data.get("restaurant_name") if isinstance(data.get("restaurant_name"), str) else "Outlet"
        mapped = []
        for inv in invites:
            if not isinstance(inv, dict):
                continue
            mapped.append(
                {
                    "role": inv.get("role"),
                    "expires_at": inv.get("expires_at"),
                    "code": inv.get("code"),
                    "link": inv.get("deep_link"),
                }
            )
        return responses.invites_list(mapped, restaurant_name=restaurant_name)

    if tool == "invite_codes_move":
        code = data.get("code")
        restaurant_name = data.get("restaurant_name")
        if isinstance(code, str) and isinstance(restaurant_name, str):
            return responses.INVITE_MOVE_SUCCESS.format(code=code, restaurant=restaurant_name)
        return "Invite moved."

    if tool == "supplier_items_search":
        items = data.get("items") if isinstance(data.get("items"), list) else []
        supplier_name = data.get("supplier_name")
        header = "🔎 Items"
        if isinstance(supplier_name, str) and supplier_name.strip():
            header = f"🔎 Items — {supplier_name.strip()}"

        if not items:
            return f"{header}\n\nNo results. Try a different search."

        lines = [header, ""]
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            supplier = item.get("supplier")
            price = item.get("min_price")
            currency = item.get("currency")
            bits = []
            if isinstance(name, str) and name.strip():
                bits.append(name.strip())
            if isinstance(supplier, str) and supplier.strip() and not supplier_name:
                bits.append(f"({supplier.strip()})")
            if price is not None:
                if isinstance(currency, str) and currency.strip():
                    bits.append(f": {price} {currency.strip()}")
                else:
                    bits.append(f": {price}")
            if bits:
                lines.append(f"{i}. " + " ".join(bits))

        if data.get("has_more") is True:
            lines.append("")
            lines.append("More available.")
        return "\n".join(lines).strip()

    return tool_response_json

