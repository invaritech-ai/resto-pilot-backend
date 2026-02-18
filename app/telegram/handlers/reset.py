"""Priority 1: Global Reset handler.

Clears navigation context and returns user to main menu.
Triggered by: home, menu, cancel, exit, /start (case-insensitive).

Action:
    - Clear: last_list_type, last_list_offset, numbered_items, active_staging_id
    - Keep: active_restaurant_id (it's a preference, not navigation)
    - Reply with Main Menu
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.user import User
from app.services.context_service import ContextService
from app.telegram.bot_api import send_message


MAIN_MENU_TEXT = """👋 What would you like to do?

/list suppliers
/add supplier
/uploads
/switch — change restaurant"""


def handle(
    update: dict,
    user: User,
    db: Session,
    ctx_svc: ContextService,
    settings: Settings,
) -> None:
    """Handle global reset: clear navigation state and show main menu.

    Args:
        update: Telegram update dict (unused but required by router interface)
        user: User ORM object
        db: Database session
        ctx_svc: ContextService for managing user.context
        settings: App settings for send_message
    """
    # Clear navigation state (keeps active_restaurant_id)
    ctx_svc.clear_navigation(user)
    db.commit()

    # Send main menu
    send_message(
        chat_id=user.chat_id,
        text=MAIN_MENU_TEXT,
        settings=settings,
    )
