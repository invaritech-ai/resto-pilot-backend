"""Tests for app/telegram/handlers/buttons.py — conf_u confirm flow.

Unit tests (TestConfirmUploadValidation, TestConfirmUploadHappyPath) use
mocked SQLAlchemy sessions and mocked bot API calls.

Integration tests (TestConfirmUploadIntegration) use a real PostgreSQL
Session (Neon) via the pg_session fixture from conftest.py. The session uses
SAVEPOINT mode so all test data is rolled back after each test. pg_trgm,
FOR UPDATE, JSONB, and CHECK constraints work natively.
Only RestaurantService is patched (membership check has no test data).
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import select

from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.inventory_balances import InventoryBalance
from app.db.models.inventory_items import InventoryItem
from app.db.models.inventory_transactions import InventoryTransaction
from app.db.models.restaurant import Restaurant
from app.db.models.user import User
from app.telegram.handlers.buttons import handle
from app.telegram.keyboards import uuid_to_hex

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

RESTAURANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
STAGING_ID = uuid.uuid4()
ITEM_ID = uuid.uuid4()

STAGING_HEX = uuid_to_hex(STAGING_ID)


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = USER_ID
    user.chat_id = 100
    user.context = {"active_staging_id": str(STAGING_ID)}
    return user


def _make_ctx_svc() -> MagicMock:
    ctx_svc = MagicMock()
    ctx_svc.set_fields = MagicMock()
    return ctx_svc


def _make_db() -> MagicMock:
    return MagicMock()


def _make_settings() -> MagicMock:
    return MagicMock()


def _make_update(callback_data: str, message_id: int = 42) -> dict:
    return {
        "callback_query": {
            "id": "cb001",
            "data": callback_data,
            "message": {
                "message_id": message_id,
                "chat": {"id": 100},
            },
        }
    }


def _make_staging(
    status: str = "pending_review",
    document_type: str = "invoice",
    line_items: list | None = None,
) -> MagicMock:
    staging = MagicMock()
    staging.id = STAGING_ID
    staging.restaurant_id = RESTAURANT_ID
    staging.status = status
    staging.document_type = document_type
    staging.extracted_data_json = {
        "line_items": line_items
        if line_items is not None
        else [{"name": "Chicken Breast", "qty": 5.0, "unit_price": 8.5, "amount": 42.5}]
    }
    return staging


def _make_inventory_item(item_id: uuid.UUID | None = None) -> MagicMock:
    item = MagicMock()
    item.id = item_id or ITEM_ID
    item.name = "Chicken Breast"
    item.name_lower = "chicken breast"
    return item


# ---------------------------------------------------------------------------
# conf_u: missing / invalid staging
# ---------------------------------------------------------------------------


class TestConfirmUploadValidation:
    def test_missing_param_answers_invalid(self):
        user = _make_user()
        db = _make_db()

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update("conf_u"),  # no staging hex
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "invalid" in mock_acq.call_args[1]["text"].lower()

    def test_invalid_hex_answers_invalid_id(self):
        user = _make_user()
        db = _make_db()

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update("conf_u:not-valid-hex"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "invalid" in mock_acq.call_args[1]["text"].lower()

    def test_staging_not_found_answers_not_found(self):
        user = _make_user()
        db = _make_db()
        db.get.return_value = None  # staging not in DB

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "not found" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()

    def test_already_confirmed_answers_already_processed(self):
        user = _make_user()
        db = _make_db()
        db.get.return_value = _make_staging(status="confirmed")

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "already" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()

    def test_wrong_document_type_answers_error(self):
        user = _make_user()
        db = _make_db()
        db.get.return_value = _make_staging(document_type="price_list")

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "invoice" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()

    def test_empty_line_items_answers_error(self):
        user = _make_user()
        db = _make_db()
        db.get.return_value = _make_staging(line_items=[])

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "no valid line items" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()

    def test_unauthorized_user_is_rejected(self):
        user = _make_user()
        db = _make_db()
        db.get.return_value = _make_staging()

        with patch("app.telegram.handlers.buttons.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            # uploaded_by != user.id, and membership check returns False
            staging = db.get.return_value
            staging.uploaded_by = uuid.uuid4()  # different user
            MockRest.return_value.user_membership_exists.return_value = False
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        mock_acq.assert_called_once()
        assert "not authorized" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()

    def test_owner_bypasses_membership_check(self):
        """uploaded_by == user.id should pass auth even if membership returns False."""
        user = _make_user()
        db = _make_db()
        staging = _make_staging(line_items=[])  # empty → early exit after auth
        staging.uploaded_by = USER_ID  # same as user.id
        db.get.return_value = staging

        with patch("app.telegram.handlers.buttons.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            MockRest.return_value.user_membership_exists.return_value = False
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        # Auth passed; empty line items → "no valid line items" (not "not authorized")
        assert "not authorized" not in mock_acq.call_args[1]["text"].lower()
        assert "no valid line items" in mock_acq.call_args[1]["text"].lower()

    def test_malformed_line_items_filtered_out(self):
        """Rows missing qty or with zero/negative qty are skipped."""
        staging = _make_staging(
            line_items=[
                {"name": "Good Item", "qty": 3.0},
                {"name": "Missing Qty"},           # no qty key → filtered
                {"name": "Zero Qty", "qty": 0.0},  # qty=0 → filtered
                {"name": "", "qty": 5.0},           # empty name → filtered
                {"qty": 2.0},                       # no name → filtered
            ]
        )
        user = _make_user()
        db = _make_db()
        db.get.return_value = staging

        with patch("app.telegram.handlers.buttons.InventoryService") as MockInv, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq, \
             patch("app.telegram.handlers.buttons.edit_message_text"):
            inv_instance = MockInv.return_value
            inv_instance.fuzzy_match_item.return_value = [(MagicMock(id=uuid.uuid4()), 0.9)]
            inv_instance.confirm_invoice.return_value = 1
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        # Only "Good Item" survives — confirm_invoice called with 1-item list
        call_kwargs = inv_instance.confirm_invoice.call_args[1]
        assert len(call_kwargs["line_items"]) == 1
        assert call_kwargs["line_items"][0]["name"] == "Good Item"

    def test_all_invalid_line_items_answers_error(self):
        staging = _make_staging(
            line_items=[
                {"name": "Missing Qty"},       # no qty
                {"name": "", "qty": 5.0},      # empty name
            ]
        )
        user = _make_user()
        db = _make_db()
        db.get.return_value = staging

        with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                _make_ctx_svc(),
                _make_settings(),
            )

        assert "no valid line items" in mock_acq.call_args[1]["text"].lower()
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# conf_u: happy path — all items auto-matched at ≥0.8
# ---------------------------------------------------------------------------


class TestConfirmUploadHappyPath:
    def _run_confirm(
        self,
        staging: MagicMock,
        fuzzy_match_return: list,
        get_or_create_return: tuple | None = None,
    ):
        """Helper: patch InventoryService, run handle, return mocks."""
        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        db.get.return_value = staging

        with patch("app.telegram.handlers.buttons.InventoryService") as MockInv, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq, \
             patch("app.telegram.handlers.buttons.edit_message_text") as mock_edit:

            inv_instance = MockInv.return_value
            inv_instance.fuzzy_match_item.return_value = fuzzy_match_return
            if get_or_create_return:
                inv_instance.get_or_create_item.return_value = get_or_create_return
            inv_instance.confirm_invoice.return_value = 1

            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                ctx_svc,
                _make_settings(),
            )

        return inv_instance, mock_acq, mock_edit, staging, ctx_svc, db

    def test_fuzzy_match_hit_calls_confirm_invoice(self):
        staging = _make_staging()
        existing_item = _make_inventory_item()

        inv, mock_acq, mock_edit, staging_obj, _, db = self._run_confirm(
            staging=staging,
            fuzzy_match_return=[(existing_item, 0.95)],
        )

        inv.confirm_invoice.assert_called_once_with(
            restaurant_id=RESTAURANT_ID,
            user_id=USER_ID,
            staging_id=STAGING_ID,
            line_items=staging.extracted_data_json["line_items"],
            resolutions={"Chicken Breast": existing_item.id},
        )

    def test_confirm_sets_staging_status_to_confirmed(self):
        staging = _make_staging()
        existing_item = _make_inventory_item()

        _, _, _, staging_obj, _, db = self._run_confirm(
            staging=staging,
            fuzzy_match_return=[(existing_item, 0.9)],
        )

        assert staging_obj.status == "confirmed"
        db.commit.assert_called_once()

    def test_confirm_clears_active_staging_id_from_context(self):
        staging = _make_staging()
        existing_item = _make_inventory_item()

        _, _, _, _, ctx_svc, _ = self._run_confirm(
            staging=staging,
            fuzzy_match_return=[(existing_item, 0.9)],
        )

        ctx_svc.set_fields.assert_called_once()
        call_kwargs = ctx_svc.set_fields.call_args[1]
        assert call_kwargs.get("active_staging_id") is None

    def test_confirm_edits_message_with_count(self):
        staging = _make_staging()
        existing_item = _make_inventory_item()

        _, mock_acq, mock_edit, _, _, _ = self._run_confirm(
            staging=staging,
            fuzzy_match_return=[(existing_item, 0.9)],
        )

        mock_edit.assert_called_once()
        edit_text = mock_edit.call_args[1]["text"]
        assert "✅" in edit_text
        assert "confirmed" in edit_text.lower()
        assert "1" in edit_text

        mock_acq.assert_called_once()
        assert "confirmed" in mock_acq.call_args[1]["text"].lower()

    def test_no_fuzzy_match_creates_new_item(self):
        """Items with no ≥0.8 match are auto-created via get_or_create_item."""
        staging = _make_staging(
            line_items=[{"name": "Brand New Item", "qty": 2.0, "unit": "kg"}]
        )
        new_item = _make_inventory_item(item_id=uuid.uuid4())
        new_item.name = "Brand New Item"

        inv, _, _, staging_obj, _, db = self._run_confirm(
            staging=staging,
            fuzzy_match_return=[],  # no match at ≥0.8
            get_or_create_return=(new_item, True),
        )

        inv.get_or_create_item.assert_called_once_with(
            restaurant_id=RESTAURANT_ID,
            name="Brand New Item",
            unit="kg",
        )
        inv.confirm_invoice.assert_called_once()
        assert staging_obj.status == "confirmed"
        db.commit.assert_called_once()

    def test_multiple_line_items_all_matched(self):
        item_id_2 = uuid.uuid4()
        staging = _make_staging(
            line_items=[
                {"name": "Chicken Breast", "qty": 5.0},
                {"name": "Olive Oil", "qty": 2.0},
            ]
        )
        item1 = _make_inventory_item(item_id=ITEM_ID)
        item1.name = "Chicken Breast"
        item2 = _make_inventory_item(item_id=item_id_2)
        item2.name = "Olive Oil"

        user = _make_user()
        ctx_svc = _make_ctx_svc()
        db = _make_db()
        db.get.return_value = staging

        with patch("app.telegram.handlers.buttons.InventoryService") as MockInv, \
             patch("app.telegram.handlers.buttons.answer_callback_query"), \
             patch("app.telegram.handlers.buttons.edit_message_text"):

            inv_instance = MockInv.return_value
            inv_instance.fuzzy_match_item.side_effect = [
                [(item1, 0.95)],   # first call → Chicken Breast
                [(item2, 0.88)],   # second call → Olive Oil
            ]
            inv_instance.confirm_invoice.return_value = 2

            handle(
                _make_update(f"conf_u:{STAGING_HEX}"),
                user,
                db,
                ctx_svc,
                _make_settings(),
            )

        inv_instance.confirm_invoice.assert_called_once_with(
            restaurant_id=RESTAURANT_ID,
            user_id=USER_ID,
            staging_id=STAGING_ID,
            line_items=staging.extracted_data_json["line_items"],
            resolutions={
                "Chicken Breast": item1.id,
                "Olive Oil": item2.id,
            },
        )
        assert staging.status == "confirmed"
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# conf_u: integration tests (real PostgreSQL session via pg_session fixture)
#
# Uses the Neon DB with SAVEPOINT-based rollback — session.commit() inside
# the handler releases a SAVEPOINT, outer transaction rolls back after the
# test. pg_trgm, FOR UPDATE, JSONB, and CHECK constraints all work natively.
# Only RestaurantService is patched (membership check has no test data).
# ---------------------------------------------------------------------------


class TestConfirmUploadIntegration:
    """End-to-end DB write verification for the conf_u confirm path."""

    def _make_real_user(self, session) -> User:
        """Create a real User row. Uses random large ints for unique telegram_id/chat_id."""
        tg_id = random.randint(10_000_000_000, 99_999_999_999)
        user = User(
            telegram_id=tg_id,
            chat_id=tg_id,
            full_name="Test User",
        )
        session.add(user)
        session.flush()
        return user

    def _make_real_restaurant(self, session, owner: User) -> Restaurant:
        """Create a real Restaurant row owned by owner."""
        restaurant = Restaurant(
            name="Test Restaurant",
            restaurant_code=uuid.uuid4().hex[:12],
            owner_user_id=owner.id,
        )
        session.add(restaurant)
        session.flush()
        return restaurant

    def _make_real_staging(
        self,
        session,
        restaurant_id: uuid.UUID,
        user_id: uuid.UUID,
        staging_id: uuid.UUID,
        line_items: list,
    ) -> FileProcessingStaging:
        staging = FileProcessingStaging(
            id=staging_id,
            restaurant_id=restaurant_id,
            uploaded_by=user_id,
            file_id="tg_file_001",
            file_unique_id="unique_001",
            status="pending_review",
            document_type="invoice",
            extracted_data_json={"line_items": line_items},
        )
        session.add(staging)
        session.flush()
        return staging

    def _make_real_item(
        self,
        session,
        restaurant_id: uuid.UUID,
        name: str = "Chicken Breast",
        unit: str = "kg",
    ) -> InventoryItem:
        item = InventoryItem(
            restaurant_id=restaurant_id,
            name=name,
            name_lower=name.lower(),
            unit=unit,
        )
        session.add(item)
        session.flush()
        return item

    def test_confirm_writes_ledger_balance_and_staging_status(self, pg_session):
        """Happy path: one line item matches existing item via pg_trgm → txn + balance
        created atomically, staging status updated to confirmed, context cleared."""
        db_user = self._make_real_user(pg_session)
        restaurant = self._make_real_restaurant(pg_session, db_user)
        staging_id = uuid.uuid4()

        # Pre-create item with exact name — pg_trgm similarity("chicken breast","chicken breast")=1.0
        item = self._make_real_item(pg_session, restaurant.id)
        staging = self._make_real_staging(
            pg_session,
            restaurant_id=restaurant.id,
            user_id=db_user.id,
            staging_id=staging_id,
            line_items=[{"name": "Chicken Breast", "qty": 5.0, "unit_price": 8.5, "amount": 42.5}],
        )
        pg_session.commit()

        user = MagicMock()
        user.id = db_user.id
        user.chat_id = 100
        user.context = {"active_staging_id": str(staging_id)}

        ctx_svc = MagicMock()
        ctx_svc.set_fields = MagicMock()

        with patch("app.telegram.handlers.buttons.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq, \
             patch("app.telegram.handlers.buttons.edit_message_text") as mock_edit:
            MockRest.return_value.user_membership_exists.return_value = True
            handle(
                _make_update(f"conf_u:{uuid_to_hex(staging_id)}"),
                user,
                pg_session,
                ctx_svc,
                _make_settings(),
            )

        # inventory_transactions: one credit row
        txns = pg_session.execute(
            select(InventoryTransaction).where(InventoryTransaction.staging_id == staging_id)
        ).scalars().all()
        assert len(txns) == 1
        assert txns[0].txn_type == "credit"
        assert float(txns[0].quantity) == 5.0
        assert txns[0].item_id == item.id
        assert txns[0].staging_id == staging_id

        # inventory_balances: one row, balance = qty
        balances = pg_session.execute(
            select(InventoryBalance).where(InventoryBalance.item_id == item.id)
        ).scalars().all()
        assert len(balances) == 1
        assert float(balances[0].balance) == 5.0
        assert balances[0].last_txn_id == txns[0].id

        # staging status updated
        pg_session.refresh(staging)
        assert staging.status == "confirmed"

        # context cleared
        ctx_svc.set_fields.assert_called_once()
        assert ctx_svc.set_fields.call_args[1].get("active_staging_id") is None

        # user feedback sent
        mock_acq.assert_called_once()
        assert "confirmed" in mock_acq.call_args[1]["text"].lower()
        mock_edit.assert_called_once()
        assert "1" in mock_edit.call_args[1]["text"]  # "1 item added"

    def test_confirm_multiple_items_accumulates_correctly(self, pg_session):
        """Two distinct items on one invoice → two ledger rows + two balance rows."""
        db_user = self._make_real_user(pg_session)
        restaurant = self._make_real_restaurant(pg_session, db_user)
        staging_id = uuid.uuid4()

        item1 = self._make_real_item(pg_session, restaurant.id, "Chicken Breast", "kg")
        item2 = self._make_real_item(pg_session, restaurant.id, "Olive Oil", "L")
        staging = self._make_real_staging(
            pg_session,
            restaurant_id=restaurant.id,
            user_id=db_user.id,
            staging_id=staging_id,
            line_items=[
                {"name": "Chicken Breast", "qty": 5.0},
                {"name": "Olive Oil", "qty": 2.0},
            ],
        )
        pg_session.commit()

        user = MagicMock()
        user.id = db_user.id
        user.chat_id = 100
        user.context = {}

        with patch("app.telegram.handlers.buttons.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.buttons.answer_callback_query"), \
             patch("app.telegram.handlers.buttons.edit_message_text"):
            MockRest.return_value.user_membership_exists.return_value = True
            handle(
                _make_update(f"conf_u:{uuid_to_hex(staging_id)}"),
                user,
                pg_session,
                MagicMock(),
                _make_settings(),
            )

        txns = pg_session.execute(
            select(InventoryTransaction).where(InventoryTransaction.staging_id == staging_id)
        ).scalars().all()
        assert len(txns) == 2

        bal1 = pg_session.execute(
            select(InventoryBalance).where(InventoryBalance.item_id == item1.id)
        ).scalar_one()
        bal2 = pg_session.execute(
            select(InventoryBalance).where(InventoryBalance.item_id == item2.id)
        ).scalar_one()
        assert float(bal1.balance) == 5.0
        assert float(bal2.balance) == 2.0

        pg_session.refresh(staging)
        assert staging.status == "confirmed"

    def test_unauthorized_user_makes_no_db_changes(self, pg_session):
        """Auth failure: no ledger rows written, staging stays pending_review."""
        db_user = self._make_real_user(pg_session)
        restaurant = self._make_real_restaurant(pg_session, db_user)
        other_user = self._make_real_user(pg_session)
        staging_id = uuid.uuid4()

        staging = self._make_real_staging(
            pg_session,
            restaurant_id=restaurant.id,
            user_id=db_user.id,
            staging_id=staging_id,
            line_items=[{"name": "Butter", "qty": 1.0}],
        )
        pg_session.commit()

        user = MagicMock()
        user.id = other_user.id  # not the uploader, not a member
        user.chat_id = 100

        with patch("app.telegram.handlers.buttons.RestaurantService") as MockRest, \
             patch("app.telegram.handlers.buttons.answer_callback_query") as mock_acq:
            MockRest.return_value.user_membership_exists.return_value = False
            handle(
                _make_update(f"conf_u:{uuid_to_hex(staging_id)}"),
                user,
                pg_session,
                MagicMock(),
                _make_settings(),
            )

        # No transactions created
        txns = pg_session.execute(
            select(InventoryTransaction).where(InventoryTransaction.staging_id == staging_id)
        ).scalars().all()
        assert len(txns) == 0

        # Staging stays pending
        pg_session.refresh(staging)
        assert staging.status == "pending_review"

        assert "not authorized" in mock_acq.call_args[1]["text"].lower()
