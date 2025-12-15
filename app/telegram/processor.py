from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.restaurant import Restaurant
from app.db.models.user import User
from app.domain.services.invite_service import InviteCodeService
from app.domain.services.restaurant_service import RestaurantService


@dataclass(frozen=True)
class TelegramResponse:
    method: str
    chat_id: int
    text: str

    def as_webhook_response(self) -> dict:
        return {"method": self.method, "chat_id": self.chat_id, "text": self.text}


def _is_superuser(*, telegram_id: int, settings: Settings) -> bool:
    return telegram_id in set(settings.telegram_superuser_ids)


def _send_message(*, chat_id: int, text: str) -> TelegramResponse:
    return TelegramResponse(method="sendMessage", chat_id=chat_id, text=text)


def _parse_start_payload(text: str) -> str | None:
    text = (text or "").strip()
    if not text.startswith("/start"):
        return None
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip() or None
    return None


def process_update(*, update: dict, session: Session, settings: Settings) -> dict | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    from_user = message.get("from", {}) or {}
    telegram_id = from_user.get("id")
    text = message.get("text") or ""

    if not isinstance(chat_id, int) or not isinstance(telegram_id, int):
        return None

    now = dt.datetime.now(dt.UTC)
    user = session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            chat_id=chat_id,
            full_name=from_user.get("first_name"),
            username=from_user.get("username"),
            state="IDLE",
            last_interaction_at=now,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        changed = False
        if user.chat_id != chat_id:
            user.chat_id = chat_id
            changed = True
        if user.last_interaction_at != now:
            user.last_interaction_at = now
            changed = True
        if changed:
            session.add(user)
            session.commit()

    payload = _parse_start_payload(text)
    if text.strip().startswith("/start"):
        if payload:
            return _handle_start_with_code(
                session=session,
                settings=settings,
                user=user,
                chat_id=chat_id,
                code=payload,
            )
        return _handle_start_no_payload(session=session, user=user, chat_id=chat_id)

    if user.state == "AWAITING_RESTAURANT_NAME":
        restaurant_name = text.strip()
        if not restaurant_name:
            return _send_message(chat_id=chat_id, text="Send a restaurant name.").as_webhook_response()

        restaurant_service = RestaurantService(session)
        restaurant = restaurant_service.create_restaurant(
            owner_user_id=user.id, name=restaurant_name
        )
        restaurant_service.add_membership(
            restaurant_id=restaurant.id,
            user_id=user.id,
            role="owner",
            invited_by=None,
        )
        user.state = "IDLE"
        session.add(user)
        session.commit()
        return _send_message(
            chat_id=chat_id,
            text=f"Restaurant created: {restaurant.name}.",
        ).as_webhook_response()

    return _send_message(chat_id=chat_id, text="Send /start to begin.").as_webhook_response()


def _handle_start_no_payload(*, session: Session, user: User, chat_id: int) -> dict:
    # Owner path: always allow creating a new restaurant.
    user.state = "AWAITING_RESTAURANT_NAME"
    session.add(user)
    session.commit()
    return _send_message(chat_id=chat_id, text="Create restaurant: send its name.").as_webhook_response()


def _handle_start_with_code(
    *,
    session: Session,
    settings: Settings,
    user: User,
    chat_id: int,
    code: str,
) -> dict:
    invite_service = InviteCodeService(session)
    invite = invite_service.get_valid_invite_by_code(code=code)
    if invite is None:
        return _send_message(
            chat_id=chat_id, text="Invite is invalid. Create your own restaurant with /start."
        ).as_webhook_response()

    if RestaurantService(session).user_membership_exists(
        restaurant_id=invite.restaurant_id, user_id=user.id
    ):
        return _send_message(chat_id=chat_id, text="You are already a member.").as_webhook_response()

    RestaurantService(session).add_membership(
        restaurant_id=invite.restaurant_id,
        user_id=user.id,
        role=invite.role,
        invited_by=invite.created_by,
    )
    invite_service.mark_used(invite=invite)

    restaurant = session.get(Restaurant, invite.restaurant_id)
    restaurant_name = restaurant.name if restaurant else "restaurant"
    return _send_message(chat_id=chat_id, text=f"Joined {restaurant_name}.").as_webhook_response()
