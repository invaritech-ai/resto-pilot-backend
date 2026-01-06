from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.db_pending_actions import DBPendingActions


class DBPendingActionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_pending(
        self,
        *,
        user_id: uuid.UUID,
        action_json: dict,
        chat_id: int | None = None,
        session_id: uuid.UUID | None = None,
        expires_at: dt.datetime | None = None,
    ) -> DBPendingActions:
        row = DBPendingActions(
            user_id=user_id,
            chat_id=chat_id,
            session_id=session_id,
            action_json=action_json,
            expires_at=expires_at or dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get_latest_pending(
        self,
        *,
        user_id: uuid.UUID,
        now: dt.datetime | None = None,
    ) -> DBPendingActions | None:
        now = now or dt.datetime.now(dt.UTC)
        return self.session.scalar(
            select(DBPendingActions)
            .where(
                DBPendingActions.user_id == user_id,
                DBPendingActions.status == "pending",
                DBPendingActions.expires_at > now,
            )
            .order_by(DBPendingActions.created_at.desc())
            .limit(1)
        )

    def confirm(
        self,
        *,
        pending: DBPendingActions,
        confirmed_at: dt.datetime | None = None,
    ) -> DBPendingActions:
        pending.status = "confirmed"
        pending.confirmed_at = confirmed_at or dt.datetime.now(dt.UTC)
        self.session.add(pending)
        self.session.commit()
        self.session.refresh(pending)
        return pending

    def cancel(
        self,
        *,
        pending: DBPendingActions,
        cancelled_at: dt.datetime | None = None,
    ) -> DBPendingActions:
        pending.status = "cancelled"
        pending.cancelled_at = cancelled_at or dt.datetime.now(dt.UTC)
        self.session.add(pending)
        self.session.commit()
        self.session.refresh(pending)
        return pending
