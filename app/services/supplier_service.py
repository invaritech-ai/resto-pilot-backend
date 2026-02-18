"""
Supplier service — global registry + restaurant link management.

Responsibilities:
    create()                       — new global supplier + immediate restaurant link
    link()                         — link existing global supplier to a restaurant
    list_for_restaurant()          — paginated active suppliers for a restaurant
    is_linked()                    — active link existence check
    fuzzy_search()                 — trigram similarity search across global registry
    list_restaurants_for_supplier() — reverse lookup: which restaurants use this supplier
    list_products_for_restaurant() — paginated (Supplier, SupplierPrice) for display
    count_products_for_restaurant() — total product count for pagination
    list_prices_for_supplier()     — paginated prices from one supplier for a restaurant

Exceptions:
    SupplierNotFoundError  — supplier_id does not exist in global registry
    AlreadyLinkedError     — supplier is already actively linked to that restaurant

Callers own the commit. This service only flushes within the caller's transaction.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.db.models.restaurant import Restaurant
from app.db.models.restaurant_suppliers import RestaurantSupplier
from app.db.models.supplier_price_lists import SupplierPriceList
from app.db.models.supplier_prices import SupplierPrice
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
        """Reverse lookup: restaurants actively linked to this supplier."""
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

    def fuzzy_search_for_restaurant(
        self,
        name: str,
        restaurant_id: uuid.UUID,
        threshold: float = 0.6,
    ) -> list[tuple[Supplier, float]]:
        """Search suppliers linked to a specific restaurant by trigram similarity.

        Unlike fuzzy_search(), this is scoped to suppliers actively linked to
        the given restaurant. Use this for commands like /prices where a global
        match could return a supplier the restaurant has never linked.

        Args:
            name:          Search query (lowercased internally).
            restaurant_id: Restrict matches to this restaurant's active links.
            threshold:     Minimum similarity score (0.0–1.0). Default 0.6.

        Returns:
            List of (Supplier, score) tuples, highest score first.
        """
        query_lower = name.strip().lower()
        score = func.similarity(Supplier.name_lower, query_lower).label("score")
        stmt = (
            select(Supplier, score)
            .join(RestaurantSupplier, RestaurantSupplier.supplier_id == Supplier.id)
            .where(
                RestaurantSupplier.restaurant_id == restaurant_id,
                RestaurantSupplier.is_active == true(),
                func.similarity(Supplier.name_lower, query_lower) >= threshold,
            )
            .order_by(score.desc())
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in rows]

    # ------------------------------------------------------------------
    # Price list queries (used by /products and /prices commands)
    # ------------------------------------------------------------------

    def list_products_for_restaurant(
        self,
        restaurant_id: uuid.UUID,
        offset: int = 0,
        limit: int = 10,
    ) -> list[tuple[Supplier, SupplierPrice]]:
        """Return paginated (Supplier, SupplierPrice) pairs for a restaurant.

        Orders by supplier name then item name. Used by /products command.
        """
        stmt = (
            select(Supplier, SupplierPrice)
            .join(SupplierPriceList, SupplierPriceList.supplier_id == Supplier.id)
            .join(SupplierPrice, SupplierPrice.price_list_id == SupplierPriceList.id)
            .where(SupplierPriceList.restaurant_id == restaurant_id)
            .order_by(Supplier.name_lower, SupplierPrice.item_name_lower)
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], row[1]) for row in rows]

    def count_products_for_restaurant(self, restaurant_id: uuid.UUID) -> int:
        """Return total product count across all price lists for a restaurant."""
        stmt = (
            select(func.count())
            .select_from(SupplierPrice)
            .join(SupplierPriceList, SupplierPrice.price_list_id == SupplierPriceList.id)
            .where(SupplierPriceList.restaurant_id == restaurant_id)
        )
        return self.session.scalar(stmt) or 0

    def list_prices_for_supplier(
        self,
        restaurant_id: uuid.UUID,
        supplier_id: uuid.UUID,
        offset: int = 0,
        limit: int = 10,
    ) -> list[tuple[SupplierPrice, dt.date | None]]:
        """Return paginated (SupplierPrice, effective_date) for a supplier + restaurant.

        Orders by effective_date descending (newest first), then item name.
        Used by /prices command.
        """
        stmt = (
            select(SupplierPrice, SupplierPriceList.effective_date)
            .join(SupplierPriceList, SupplierPrice.price_list_id == SupplierPriceList.id)
            .where(
                SupplierPriceList.restaurant_id == restaurant_id,
                SupplierPrice.supplier_id == supplier_id,
            )
            .order_by(
                SupplierPriceList.effective_date.desc().nulls_last(),
                SupplierPrice.item_name_lower,
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [(row[0], row[1]) for row in rows]

    def count_prices_for_supplier(
        self,
        restaurant_id: uuid.UUID,
        supplier_id: uuid.UUID,
    ) -> int:
        """Return total price count for a supplier + restaurant."""
        stmt = (
            select(func.count())
            .select_from(SupplierPrice)
            .join(SupplierPriceList, SupplierPrice.price_list_id == SupplierPriceList.id)
            .where(
                SupplierPriceList.restaurant_id == restaurant_id,
                SupplierPrice.supplier_id == supplier_id,
            )
        )
        return self.session.scalar(stmt) or 0
