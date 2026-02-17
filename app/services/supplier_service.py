"""
Supplier service — global registry + restaurant link management.

Responsibilities:
    create()                    — new global supplier + immediate restaurant link
    link()                      — link existing global supplier to a restaurant
    list_for_restaurant()       — paginated active suppliers for a restaurant
    is_linked()                 — active link existence check
    fuzzy_search()              — trigram similarity search across global registry
    list_restaurants_for_supplier() — reverse lookup: which restaurants use this supplier

Exceptions:
    SupplierNotFoundError  — supplier_id does not exist in global registry
    AlreadyLinkedError     — supplier is already actively linked to that restaurant

Callers own the commit. This service only flushes within the caller's transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSupplier
from app.db.models.suppliers import Supplier


class SupplierNotFoundError(Exception):
    pass


class AlreadyLinkedError(Exception):
    pass


class SupplierService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        user_id: uuid.UUID,
        restaurant_id: uuid.UUID,
        **kwargs: object,
    ) -> Supplier:
        """Create a new global supplier and link it to the restaurant.

        Args:
            name:          Display name (stored after strip).
            user_id:       Who is creating (recorded as added_by on the link).
            restaurant_id: Restaurant to link immediately.
            **kwargs:      Optional supplier fields: contact_name, phone, email,
                           default_currency, notes.

        Returns:
            The newly created Supplier ORM object.
        """
        clean_name = name.strip()
        supplier = Supplier(
            name=clean_name,
            name_lower=clean_name.lower(),
            **kwargs,
        )
        self.session.add(supplier)

        link = RestaurantSupplier(
            restaurant_id=restaurant_id,
            supplier_id=supplier.id,
            added_by=user_id,
        )
        self.session.add(link)
        self.session.flush()
        return supplier

    # ------------------------------------------------------------------
    # Link
    # ------------------------------------------------------------------

    def link(
        self,
        supplier_id: uuid.UUID,
        restaurant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> RestaurantSupplier:
        """Link an existing global supplier to a restaurant.

        Raises:
            SupplierNotFoundError: supplier_id does not exist.
            AlreadyLinkedError:    active link already exists.

        If an inactive link exists it is reactivated rather than duplicated.
        """
        supplier = self.session.scalar(select(Supplier).where(Supplier.id == supplier_id))
        if supplier is None:
            raise SupplierNotFoundError(f"Supplier {supplier_id} not found")

        existing = self.session.scalar(
            select(RestaurantSupplier).where(
                RestaurantSupplier.supplier_id == supplier_id,
                RestaurantSupplier.restaurant_id == restaurant_id,
            )
        )

        if existing is not None:
            if existing.is_active:
                raise AlreadyLinkedError(
                    f"Supplier {supplier_id} is already linked to restaurant {restaurant_id}"
                )
            # Reactivate a previously soft-deleted link.
            existing.is_active = True
            self.session.add(existing)
            self.session.flush()
            return existing

        link = RestaurantSupplier(
            supplier_id=supplier_id,
            restaurant_id=restaurant_id,
            added_by=user_id,
        )
        self.session.add(link)
        self.session.flush()
        return link

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_for_restaurant(
        self,
        restaurant_id: uuid.UUID,
        offset: int = 0,
        limit: int = 10,
    ) -> list[Supplier]:
        """Return active suppliers linked to a restaurant, ordered by name_lower."""
        stmt = (
            select(Supplier)
            .join(RestaurantSupplier, RestaurantSupplier.supplier_id == Supplier.id)
            .where(
                RestaurantSupplier.restaurant_id == restaurant_id,
                RestaurantSupplier.is_active == true(),
            )
            .order_by(Supplier.name_lower)
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def list_restaurants_for_supplier(self, supplier_id: uuid.UUID) -> list[Restaurant]:
        """Reverse lookup: restaurants actively linked to this supplier.

        Strictly one restaurant right now (single-outlet), but the query is
        restaurant-agnostic for future multi-outlet expansion.
        """
        stmt = (
            select(Restaurant)
            .join(RestaurantSupplier, RestaurantSupplier.restaurant_id == Restaurant.id)
            .where(
                RestaurantSupplier.supplier_id == supplier_id,
                RestaurantSupplier.is_active == true(),
            )
        )
        return list(self.session.scalars(stmt).all())

    def is_linked(self, supplier_id: uuid.UUID, restaurant_id: uuid.UUID) -> bool:
        """Return True if an active link exists between supplier and restaurant."""
        link = self.session.scalar(
            select(RestaurantSupplier).where(
                RestaurantSupplier.supplier_id == supplier_id,
                RestaurantSupplier.restaurant_id == restaurant_id,
                RestaurantSupplier.is_active == true(),
            )
        )
        return link is not None

    def fuzzy_search(
        self,
        name: str,
        threshold: float = 0.75,
    ) -> list[tuple[Supplier, float]]:
        """Search global supplier registry by trigram similarity.

        Args:
            name:      Search query (lowercased internally to match name_lower index).
            threshold: Minimum similarity score (0.0–1.0). Default 0.75.

        Returns:
            List of (Supplier, score) tuples, highest score first.
        """
        query_lower = name.strip().lower()
        score = func.similarity(Supplier.name_lower, query_lower).label("score")
        stmt = (
            select(Supplier, score)
            .where(func.similarity(Supplier.name_lower, query_lower) >= threshold)
            .order_by(score.desc())
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in rows]
