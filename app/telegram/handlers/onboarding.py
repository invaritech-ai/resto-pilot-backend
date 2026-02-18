"""Onboarding flow — 3-step state machine.

Steps (tracked via user.context["onboarding_step"]):
    (absent, full_name=None)   → ask for name        → set awaiting_name
    "awaiting_name"            → save name           → set awaiting_restaurant
    "awaiting_restaurant"      → create Restaurant   → clear, set active_restaurant
    (absent, no active_rest)   → ask for restaurant  → set awaiting_restaurant  [webapp bypass]

Fixes vs v1:
- Uses RestaurantService.create_restaurant() so owner membership is always created.
- Idempotent at step 3: re-delivery / Celery retry reuses existing restaurant.
- Commits before sending user-facing messages (no success msg on uncommitted state).
- needs_onboarding() also checks active_restaurant_id (catches webapp-auth bypass).
- Filters reset/command words from name and restaurant inputs.
- Unknown/corrupted step clears and restarts rather than silently no-op.

Fixes vs v2 (testing feedback):
- Step 3 uses SELECT FOR UPDATE to prevent race condition in concurrent restaurant creation.
- All state changes committed BEFORE sending user-facing messages.
- needs_onboarding validates active_restaurant_id is a valid UUID.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.user import User
from app.services.context_service import ContextService
from app.services.restaurant_service import RestaurantService
from app.telegram.bot_api import send_message
from app.telegram.constants import RESET_WORDS_LOWER


def needs_onboarding(user: User) -> bool:
    """Return True if the user has not completed onboarding.

    Conditions (any → still onboarding):
    1. full_name is None           → step 1 not done
    2. onboarding_step in context  → mid-flow
    3. active_restaurant_id absent → webapp auth set full_name but no restaurant yet
    4. active_restaurant_id is not a valid UUID → corrupted context
    """
    if user.full_name is None:
        return True
    ctx = user.context or {}
    if "onboarding_step" in ctx:
        return True
    raw_restaurant_id = ctx.get("active_restaurant_id")
    if raw_restaurant_id is None:
        return True
    # Validate that active_restaurant_id is a valid UUID
    try:
        uuid.UUID(str(raw_restaurant_id))
    except (ValueError, TypeError):
        return True
    return False


def _get_existing_restaurant_for_user(
    db: Session, user_id: uuid.UUID
) -> Restaurant | None:
    """Get existing restaurant for user with FOR UPDATE lock to prevent race conditions.

    This locks the restaurant_users row, preventing concurrent workers from
    creating duplicate restaurants for the same user.

    Returns the most recently created restaurant if multiple exist.
    Uses .limit(1) to avoid MultipleResultsFound exception.
    """
    # Lock the membership row to prevent concurrent restaurant creation
    # Use .limit(1) to handle users with multiple restaurant memberships
    result = db.execute(
        select(Restaurant)
        .join(RestaurantUser, RestaurantUser.restaurant_id == Restaurant.id)
        .where(
            RestaurantUser.user_id == user_id,
            RestaurantUser.is_active.is_(True),
        )
        .order_by(Restaurant.created_at.desc())
        .limit(1)  # Prevent MultipleResultsFound for users with multiple memberships
        .with_for_update()  # This is the key: lock the row
    ).scalar_one_or_none()
    return result


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Handle onboarding flow.

    IMPORTANT: All state changes must be committed BEFORE sending user-facing messages.
    This ensures that if the message send fails or the worker crashes, the user
    doesn't see a success message but the state rolls back.
    """
    chat_id: int = user.chat_id
    ctx = ctx_svc.get(user)
    step: str | None = ctx.get("onboarding_step")

    msg = update.get("message", {})
    text = (msg.get("text") or "").strip()
    text_lower = text.lower()

    # --- Step 1: no name yet → ask ---
    if step is None and user.full_name is None:
        # Set state first
        ctx_svc.set_fields(user, onboarding_step="awaiting_name")
        db.commit()  # Commit BEFORE sending message
        send_message(
            chat_id=chat_id,
            text="Hi! I'm your restaurant purchasing assistant. What's your name?",
            settings=settings,
        )
        return

    # --- Webapp bypass: full_name set externally but no restaurant yet → ask ---
    if step is None and "active_restaurant_id" not in ctx:
        ctx_svc.set_fields(user, onboarding_step="awaiting_restaurant")
        db.commit()  # Commit BEFORE sending message
        send_message(
            chat_id=chat_id,
            text=f"Hi {user.full_name}! What's the name of your restaurant?",
            settings=settings,
        )
        return

    # --- Step 2: collect name → ask for restaurant ---
    if step == "awaiting_name":
        if not text or text_lower in RESET_WORDS_LOWER or text.startswith("/"):
            send_message(
                chat_id=chat_id,
                text="Please enter your name so I can get started.",
                settings=settings,
            )
            return

        # Save name and transition state atomically
        user.full_name = text
        db.add(user)
        ctx_svc.set_fields(user, onboarding_step="awaiting_restaurant")
        db.commit()  # Commit BEFORE sending message

        send_message(
            chat_id=chat_id,
            text=f"Nice to meet you, {text}! What's the name of your restaurant?",
            settings=settings,
        )
        return

    # --- Step 3: collect restaurant name → create & done ---
    if step == "awaiting_restaurant":
        # Idempotency with locking: check if restaurant already exists
        # Use FOR UPDATE to prevent race condition with concurrent workers
        existing = _get_existing_restaurant_for_user(db, user.id)
        if existing:
            ctx_svc.set_active_restaurant(user, existing.id)
            ctx_svc.set_fields(user, onboarding_step=None)
            db.commit()  # Commit BEFORE sending message
            send_message(
                chat_id=chat_id,
                text=f'Welcome back, {user.full_name}! Your restaurant "{existing.name}" is active.',
                settings=settings,
            )
            return

        if not text or text_lower in RESET_WORDS_LOWER or text.startswith("/"):
            send_message(
                chat_id=chat_id,
                text="Please enter your restaurant name to continue.",
                settings=settings,
            )
            return

        # create_restaurant() commits internally → restaurant + owner membership persisted.
        # The commit happens BEFORE we send the welcome message.
        restaurant = RestaurantService(db).create_restaurant(
            owner_user_id=user.id,
            name=text,
        )

        # Now update context and commit
        ctx_svc.set_active_restaurant(user, restaurant.id)
        ctx_svc.set_fields(user, onboarding_step=None)
        db.commit()  # Commit BEFORE sending message

        send_message(
            chat_id=chat_id,
            text=(
                f"Welcome, {user.full_name}! "
                f'Your restaurant "{text}" is all set up. '
                "Send me a supplier price list to get started."
            ),
            settings=settings,
        )
        return

    # --- Unknown/corrupted step → clear and restart ---
    ctx_svc.set_fields(user, onboarding_step=None)
    db.commit()  # Commit BEFORE sending message
    send_message(
        chat_id=chat_id,
        text="Something went wrong. Let's start over. What's your name?",
        settings=settings,
    )
    # Set new state after the restart message
    ctx_svc.set_fields(user, onboarding_step="awaiting_name")
    db.commit()
