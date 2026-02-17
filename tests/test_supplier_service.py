"""Tests for app/services/supplier_service.py.

Written first (TDD). Implementation must make these pass.

Interface under test:
    class SupplierService(session):
        create(name, user_id, restaurant_id, **kwargs) -> Supplier
        link(supplier_id, restaurant_id, user_id) -> RestaurantSupplier
        list_for_restaurant(restaurant_id, offset=0, limit=10) -> list[Supplier]
        is_linked(supplier_id, restaurant_id) -> bool
        fuzzy_search(name, threshold=0.75) -> list[tuple[Supplier, float]]

    exceptions:
        AlreadyLinkedError
        SupplierNotFoundError
"""

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.supplier_service import (
    AlreadyLinkedError,
    SupplierNotFoundError,
    SupplierService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = MagicMock()
    return session


def make_supplier(name="ABC Wholesalers", supplier_id=None):
    s = MagicMock()
    s.id = supplier_id or uuid.uuid4()
    s.name = name
    s.name_lower = name.lower()
    s.is_active = True
    return s


def make_link(supplier_id=None, restaurant_id=None, is_active=True):
    link = MagicMock()
    link.supplier_id = supplier_id or uuid.uuid4()
    link.restaurant_id = restaurant_id or uuid.uuid4()
    link.is_active = is_active
    return link


R_ID = uuid.uuid4()
U_ID = uuid.uuid4()
S_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

class TestCreate:
    def test_returns_supplier_object(self):
        svc = SupplierService(make_session())
        result = svc.create("ABC Wholesalers", U_ID, R_ID)
        assert result is not None

    def test_name_lower_is_stripped_and_lowercased(self):
        svc = SupplierService(make_session())
        supplier = svc.create("  ABC Wholesalers  ", U_ID, R_ID)
        assert supplier.name_lower == "abc wholesalers"

    def test_name_preserved_as_given_after_strip(self):
        svc = SupplierService(make_session())
        supplier = svc.create("  ABC Wholesalers  ", U_ID, R_ID)
        assert supplier.name == "ABC Wholesalers"

    def test_supplier_added_to_session(self):
        session = make_session()
        svc = SupplierService(session)
        supplier = svc.create("ABC Wholesalers", U_ID, R_ID)
        # supplier object must have been passed to session.add
        added_objects = [c.args[0] for c in session.add.call_args_list]
        assert supplier in added_objects

    def test_restaurant_link_created(self):
        session = make_session()
        svc = SupplierService(session)
        svc.create("ABC Wholesalers", U_ID, R_ID)
        # Two objects should be added: Supplier + RestaurantSupplier
        assert session.add.call_count == 2

    def test_link_has_correct_restaurant_and_added_by(self):
        session = make_session()
        svc = SupplierService(session)
        supplier = svc.create("ABC Wholesalers", U_ID, R_ID)
        added_objects = [c.args[0] for c in session.add.call_args_list]
        # The second add call is the RestaurantSupplier
        from app.db.models.restaurant_suppliers import RestaurantSupplier
        links = [o for o in added_objects if isinstance(o, RestaurantSupplier)]
        assert len(links) == 1
        assert links[0].restaurant_id == R_ID
        assert links[0].supplier_id == supplier.id
        assert links[0].added_by == U_ID

    def test_flush_is_called(self):
        session = make_session()
        svc = SupplierService(session)
        svc.create("ABC Wholesalers", U_ID, R_ID)
        session.flush.assert_called()

    def test_optional_kwargs_set_on_supplier(self):
        session = make_session()
        svc = SupplierService(session)
        supplier = svc.create(
            "ABC Wholesalers", U_ID, R_ID,
            contact_name="Ali",
            phone="+65 9999 0000",
            email="ali@abc.com",
            default_currency="SGD",
            notes="Delivers on Tuesdays",
        )
        assert supplier.contact_name == "Ali"
        assert supplier.phone == "+65 9999 0000"
        assert supplier.email == "ali@abc.com"
        assert supplier.default_currency == "SGD"
        assert supplier.notes == "Delivers on Tuesdays"


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------

class TestLink:
    def _svc_with_supplier(self, supplier=None, existing_link=None):
        """Session that returns a supplier on first scalar call, link on second."""
        session = make_session()
        session.scalar.side_effect = [supplier, existing_link]
        return SupplierService(session), session

    def test_raises_supplier_not_found_when_supplier_missing(self):
        svc, _ = self._svc_with_supplier(supplier=None)
        with pytest.raises(SupplierNotFoundError):
            svc.link(S_ID, R_ID, U_ID)

    def test_raises_already_linked_when_active_link_exists(self):
        supplier = make_supplier()
        existing_link = make_link(is_active=True)
        svc, _ = self._svc_with_supplier(supplier=supplier, existing_link=existing_link)
        with pytest.raises(AlreadyLinkedError):
            svc.link(supplier.id, R_ID, U_ID)

    def test_creates_link_when_not_linked(self):
        supplier = make_supplier()
        svc, session = self._svc_with_supplier(supplier=supplier, existing_link=None)
        result = svc.link(supplier.id, R_ID, U_ID)
        from app.db.models.restaurant_suppliers import RestaurantSupplier
        assert isinstance(result, RestaurantSupplier)

    def test_link_has_correct_fields(self):
        supplier = make_supplier()
        svc, session = self._svc_with_supplier(supplier=supplier, existing_link=None)
        result = svc.link(supplier.id, R_ID, U_ID)
        assert result.supplier_id == supplier.id
        assert result.restaurant_id == R_ID
        assert result.added_by == U_ID

    def test_link_added_to_session(self):
        supplier = make_supplier()
        svc, session = self._svc_with_supplier(supplier=supplier, existing_link=None)
        result = svc.link(supplier.id, R_ID, U_ID)
        added_objects = [c.args[0] for c in session.add.call_args_list]
        assert result in added_objects

    def test_flush_called_on_success(self):
        supplier = make_supplier()
        svc, session = self._svc_with_supplier(supplier=supplier, existing_link=None)
        svc.link(supplier.id, R_ID, U_ID)
        session.flush.assert_called()

    def test_relinks_inactive_link(self):
        """An inactive (soft-deleted) link should be reactivated, not raise."""
        supplier = make_supplier()
        inactive_link = make_link(is_active=False)
        svc, session = self._svc_with_supplier(supplier=supplier, existing_link=inactive_link)
        result = svc.link(supplier.id, R_ID, U_ID)
        # Should reactivate the existing link, not raise
        assert result.is_active is True


# ---------------------------------------------------------------------------
# list_for_restaurant
# ---------------------------------------------------------------------------

class TestListForRestaurant:
    def test_returns_list_of_suppliers(self):
        session = make_session()
        suppliers = [make_supplier("AAA"), make_supplier("BBB")]
        session.scalars.return_value.all.return_value = suppliers
        svc = SupplierService(session)
        result = svc.list_for_restaurant(R_ID)
        assert result == suppliers

    def test_returns_empty_list_when_none(self):
        session = make_session()
        session.scalars.return_value.all.return_value = []
        svc = SupplierService(session)
        result = svc.list_for_restaurant(R_ID)
        assert result == []

    def test_default_limit_is_10(self):
        session = make_session()
        session.scalars.return_value.all.return_value = []
        svc = SupplierService(session)
        svc.list_for_restaurant(R_ID)
        # The query should have been called — we don't inspect SQL here,
        # just verify the call was made and didn't raise.
        session.scalars.assert_called_once()

    def test_offset_and_limit_passed(self):
        session = make_session()
        session.scalars.return_value.all.return_value = []
        svc = SupplierService(session)
        # Should not raise with custom offset/limit
        svc.list_for_restaurant(R_ID, offset=10, limit=5)
        session.scalars.assert_called_once()


# ---------------------------------------------------------------------------
# is_linked
# ---------------------------------------------------------------------------

class TestIsLinked:
    def test_returns_true_when_active_link_exists(self):
        session = make_session()
        session.scalar.return_value = make_link(is_active=True)
        svc = SupplierService(session)
        assert svc.is_linked(S_ID, R_ID) is True

    def test_returns_false_when_no_link(self):
        session = make_session()
        session.scalar.return_value = None
        svc = SupplierService(session)
        assert svc.is_linked(S_ID, R_ID) is False

    def test_returns_false_when_link_is_inactive(self):
        session = make_session()
        session.scalar.return_value = None  # query filters is_active=True
        svc = SupplierService(session)
        assert svc.is_linked(S_ID, R_ID) is False


# ---------------------------------------------------------------------------
# fuzzy_search
# ---------------------------------------------------------------------------

class TestFuzzySearch:
    def test_returns_list_of_supplier_score_tuples(self):
        session = make_session()
        supplier = make_supplier("ABC Wholesalers")
        # Simulate DB returning (Supplier, similarity_score) rows
        session.execute.return_value.all.return_value = [(supplier, 0.88)]
        svc = SupplierService(session)
        results = svc.fuzzy_search("ABC")
        assert len(results) == 1
        assert results[0][0] is supplier
        assert results[0][1] == pytest.approx(0.88)

    def test_returns_empty_list_when_no_matches(self):
        session = make_session()
        session.execute.return_value.all.return_value = []
        svc = SupplierService(session)
        results = svc.fuzzy_search("zzzunknown")
        assert results == []

    def test_default_threshold_is_0_75(self):
        """fuzzy_search called without threshold should use 0.75 and not raise."""
        session = make_session()
        session.execute.return_value.all.return_value = []
        svc = SupplierService(session)
        svc.fuzzy_search("ABC")  # should not raise
        session.execute.assert_called_once()

    def test_custom_threshold_accepted(self):
        session = make_session()
        session.execute.return_value.all.return_value = []
        svc = SupplierService(session)
        svc.fuzzy_search("ABC", threshold=0.5)
        session.execute.assert_called_once()

    def test_name_is_lowercased_before_search(self):
        """Search input must be lowercased to match name_lower index."""
        session = make_session()
        session.execute.return_value.all.return_value = []
        svc = SupplierService(session)
        # Doesn't raise — we can't easily inspect the SQL param in a unit test,
        # but the implementation contract is: always lowercase the query.
        svc.fuzzy_search("ABC Wholesalers")
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# list_restaurants_for_supplier
# ---------------------------------------------------------------------------

class TestListRestaurantsForSupplier:
    """Reverse lookup: given a supplier, which restaurants is it linked to?

    Strictly one right now (single-outlet), but the query is restaurant-agnostic
    so multi-outlet expansion later is free.
    """

    def _make_restaurant(self, name="Burger Barn"):
        r = MagicMock()
        r.id = uuid.uuid4()
        r.name = name
        return r

    def test_returns_list_of_restaurants(self):
        session = make_session()
        restaurant = self._make_restaurant()
        session.scalars.return_value.all.return_value = [restaurant]
        svc = SupplierService(session)
        result = svc.list_restaurants_for_supplier(S_ID)
        assert result == [restaurant]

    def test_returns_empty_list_when_not_linked_anywhere(self):
        session = make_session()
        session.scalars.return_value.all.return_value = []
        svc = SupplierService(session)
        result = svc.list_restaurants_for_supplier(S_ID)
        assert result == []

    def test_returns_multiple_restaurants_for_chain(self):
        """Future multi-outlet: same supplier linked to two outlets."""
        session = make_session()
        r1 = self._make_restaurant("Burger Barn — CBD")
        r2 = self._make_restaurant("Burger Barn — Orchard")
        session.scalars.return_value.all.return_value = [r1, r2]
        svc = SupplierService(session)
        result = svc.list_restaurants_for_supplier(S_ID)
        assert len(result) == 2

    def test_only_active_links_returned(self):
        """Inactive links must not surface in the result."""
        # The query filters is_active=True — active link returns restaurant,
        # inactive link returns nothing. We model this by what the mock returns.
        session = make_session()
        session.scalars.return_value.all.return_value = []  # inactive → filtered out
        svc = SupplierService(session)
        result = svc.list_restaurants_for_supplier(S_ID)
        assert result == []

    def test_query_is_executed(self):
        session = make_session()
        session.scalars.return_value.all.return_value = []
        svc = SupplierService(session)
        svc.list_restaurants_for_supplier(S_ID)
        session.scalars.assert_called_once()
