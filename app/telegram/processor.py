from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.user import User
from app.domain.services.invite_service import (
    InviteCodeExpiredError,
    InviteCodeNotValidError,
    InviteCodeService,
    InviteCodeUsedError,
)
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate


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


logger = logging.getLogger(__name__)

COLLECT_PHONE_STATE = "COLLECT_PHONE"
IDLE_STATE = "IDLE"


def _first_name_from_full_name(full_name: str | None) -> str | None:
    if not isinstance(full_name, str):
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


def _phone_prompt(*, full_name: str | None) -> str:
    first_name = _first_name_from_full_name(full_name)
    name_prefix = f"Hi {first_name}. " if first_name else "Hi. "
    return name_prefix + "What's your phone number (include country code, e.g. +1 415 555 0101)?"


def _parse_start_code(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/start"):
        return None
    parts = stripped.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return None


def process_update(
    *, update: dict, session: Session, settings: Settings
) -> dict | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    user_info = message.get("from") or {}
    telegram_id = user_info.get("id")

    if not isinstance(chat_id, int) or not isinstance(text, str) or not text.strip():
        return None

    if isinstance(telegram_id, int) and text.strip().startswith("/start"):
        payload = TelegramUserCreate(
            telegram_id=telegram_id,
            chat_id=chat_id,
            first_name=user_info.get("first_name"),
            last_name=user_info.get("last_name"),
            username=user_info.get("username"),
        )
        user = UserService(session).get_or_create(payload)
        db_user = session.scalar(select(User).where(User.id == user.id))
        if db_user is None:
            return _send_message(chat_id=chat_id, text="Welcome!").as_webhook_response()

        code = _parse_start_code(text)
        if code is None:
            if not (isinstance(db_user.phone, str) and db_user.phone.strip()):
                db_user.state = COLLECT_PHONE_STATE
                session.add(db_user)
                session.commit()
                return _send_message(
                    chat_id=chat_id,
                    text=_phone_prompt(full_name=db_user.full_name),
                ).as_webhook_response()
            if db_user.state != IDLE_STATE:
                db_user.state = IDLE_STATE
                session.add(db_user)
                session.commit()
            return _send_message(
                chat_id=chat_id,
                text=(
                    "Welcome! You're registered.\n"
                    "If you have an invite link/code, use it like: /start CODE"
                ),
            ).as_webhook_response()

        try:
            membership, restaurant = InviteCodeService(session).accept_invite(
                code=code, user_id=user.id
            )
        except InviteCodeUsedError:
            logger.warning(
                "telegram_invite_code_used",
                extra={"telegram_id": telegram_id, "chat_id": chat_id, "code": code},
            )
            return _send_message(
                chat_id=chat_id,
                text="That invite code has already been used. Please ask for a new one.",
            ).as_webhook_response()
        except InviteCodeExpiredError:
            logger.warning(
                "telegram_invite_code_expired",
                extra={"telegram_id": telegram_id, "chat_id": chat_id, "code": code},
            )
            return _send_message(
                chat_id=chat_id,
                text="That invite code has expired. Please ask for a new one.",
            ).as_webhook_response()
        except InviteCodeNotValidError:
            logger.warning(
                "telegram_invite_code_invalid",
                extra={"telegram_id": telegram_id, "chat_id": chat_id, "code": code},
            )
            return _send_message(
                chat_id=chat_id,
                text="That invite code is invalid. Please check it or ask for a new one.",
            ).as_webhook_response()
        except Exception as e:
            logger.exception(
                "telegram_invite_accept_failed",
                extra={
                    "error": repr(e),
                    "telegram_id": telegram_id,
                    "chat_id": chat_id,
                    "code": code,
                },
            )
            return _send_message(
                chat_id=chat_id,
                text="Something went wrong while processing the invite. Please try again.",
            ).as_webhook_response()

        restaurant_name = restaurant.name if restaurant is not None else "the restaurant"
        joined_text = f"You're now added to {restaurant_name} as {membership.role}."
        if not (isinstance(db_user.phone, str) and db_user.phone.strip()):
            db_user.state = COLLECT_PHONE_STATE
            session.add(db_user)
            session.commit()
            return _send_message(
                chat_id=chat_id,
                text=joined_text + "\n" + _phone_prompt(full_name=db_user.full_name),
            ).as_webhook_response()
        if db_user.state != IDLE_STATE:
            db_user.state = IDLE_STATE
            session.add(db_user)
            session.commit()
        return _send_message(
            chat_id=chat_id,
            text=joined_text,
        ).as_webhook_response()

    if text.strip() == "/start":
        return _send_message(
            chat_id=chat_id,
            text="Welcome! Please message me from a Telegram account.",
        ).as_webhook_response()

    return _send_message(chat_id=chat_id, text=text).as_webhook_response()
