"""Onboarding flow — 3-step state machine.

Steps (tracked via user.context["onboarding_step"]):
    (absent)               → ask for name        → set awaiting_name
    "awaiting_name"        → save name           → set awaiting_restaurant
    "awaiting_restaurant"  → create Restaurant   → clear, set active_restaurant
"""

from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.restaurant import Restaurant
from app.db.models.user import User
from app.services.context_service import ContextService
from app.telegram.bot_api import send_message


def needs_onboarding(user: User) -> bool:
    if user.full_name is None:
        return True
    ctx = user.context or {}
    return "onboarding_step" in ctx


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    chat_id: int = user.chat_id
    ctx = ctx_svc.get(user)
    step: str | None = ctx.get("onboarding_step")

    msg = update.get("message", {})
    text = (msg.get("text") or "").strip()

    # --- Step 1: no name yet → ask ---
    if step is None:
        send_message(
            chat_id=chat_id,
            text="Hi! I'm your restaurant purchasing assistant. What's your name?",
            settings=settings,
        )
        ctx_svc.set_fields(user, onboarding_step="awaiting_name")
        return

    # --- Step 2: collect name → ask for restaurant ---
    if step == "awaiting_name":
        if not text:
            send_message(
                chat_id=chat_id,
                text="Please enter your name so I can get started.",
                settings=settings,
            )
            return
        user.full_name = text
        db.add(user)
        db.flush()
        send_message(
            chat_id=chat_id,
            text=f"Nice to meet you, {text}! What's the name of your restaurant?",
            settings=settings,
        )
        ctx_svc.set_fields(user, onboarding_step="awaiting_restaurant")
        return

    # --- Step 3: collect restaurant name → create & done ---
    if step == "awaiting_restaurant":
        if not text:
            send_message(
                chat_id=chat_id,
                text="Please enter your restaurant name to continue.",
                settings=settings,
            )
            return
        code = secrets.token_hex(4).upper()  # e.g. "A3F19C2B"
        restaurant = Restaurant(
            name=text,
            restaurant_code=code,
            owner_user_id=user.id,
        )
        db.add(restaurant)
        db.flush()
        ctx_svc.set_active_restaurant(user, restaurant.id)
        ctx_svc.set_fields(user, onboarding_step=None)
        send_message(
            chat_id=chat_id,
            text=(
                f"Welcome, {user.full_name}! "
                f"Your restaurant \"{text}\" is all set up. "
                "Send me a supplier price list to get started."
            ),
            settings=settings,
        )
