from __future__ import annotations

import datetime as dt
import secrets
import string
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.invite_codes import InviteCodes
from app.db.models.restaurant_user import RestaurantUser


class InviteCodeNotValidError(ValueError):
    pass


class InviteCodePermissionError(PermissionError):
    pass


class InviteCodeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_invite_code(
        self,
        *,
        restaurant_id: uuid.UUID,
        target_role: str,
        created_by_user_id: uuid.UUID | None,
        created_by_is_superuser: bool,
        expires_at: dt.datetime | None = None,
        code_length: int = 10,
    ) -> InviteCodes:
        if target_role not in {"owner", "staff"}:
            raise ValueError("target_role must be 'owner' or 'staff'")

        if not created_by_is_superuser:
            if created_by_user_id is None:
                raise InviteCodePermissionError("created_by_user_id required")

            is_owner = self.session.scalar(
                select(RestaurantUser.id).where(
                    RestaurantUser.restaurant_id == restaurant_id,
                    RestaurantUser.user_id == created_by_user_id,
                    RestaurantUser.role == "owner",
                    RestaurantUser.status == "active",
                )
            )
            if not is_owner:
                raise InviteCodePermissionError("Only owners can create invites")

        if code_length < 8 or code_length > 10:
            raise ValueError("code_length must be 8..10")

        alphabet = string.ascii_uppercase + string.digits
        for _ in range(20):
            code = "".join(secrets.choice(alphabet) for _ in range(code_length))
            invite = InviteCodes(
                code=code,
                restaurant_id=restaurant_id,
                role=target_role,
                expires_at=expires_at,
                created_by=created_by_user_id,
            )
            self.session.add(invite)
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                continue
            self.session.refresh(invite)
            return invite

        raise RuntimeError("Failed to generate a unique invite code")

    @staticmethod
    def deep_link(*, bot_username: str, code: str) -> str:
        bot_username = bot_username.lstrip("@")
        return f"t.me/{bot_username}?start={code}"

    def get_valid_invite_by_code(
        self, *, code: str, now: dt.datetime | None = None
    ) -> InviteCodes | None:
        now = now or dt.datetime.now(dt.UTC)
        invite = self.session.scalar(select(InviteCodes).where(InviteCodes.code == code))
        if invite is None:
            return None
        if invite.used_at is not None:
            return None
        if invite.expires_at is not None and invite.expires_at <= now:
            return None
        return invite

    def mark_used(self, *, invite: InviteCodes, used_at: dt.datetime | None = None) -> None:
        invite.used_at = used_at or dt.datetime.now(dt.UTC)
        self.session.add(invite)
        self.session.commit()
