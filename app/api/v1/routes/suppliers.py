import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep
from app.db.models.restaurant_suppliers import RestaurantSuppliers
from app.db.models.restaurant_user import RestaurantUser
from app.db.models.suppliers import Suppliers

router = APIRouter()


@router.post("/suppliers/{supplier_id}/restaurants/{restaurant_id}")
def associate_supplier_to_restaurant(
    supplier_id: str,
    restaurant_id: str,
    user_id: str = Form(...),
    status_value: str = Form("active"),
    account_number: str | None = Form(None),
    default_currency: str | None = Form(None),
    lead_time_days: int | None = Form(None),
    notes: str | None = Form(None),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Link an existing supplier to a restaurant/outlet with restaurant-specific settings.

    This is intentionally separate from price list upload: price lists can be uploaded and processed
    without a restaurant association, then linked later via this endpoint.
    """
    try:
        supplier_uuid = uuid.UUID(supplier_id)
        restaurant_uuid = uuid.UUID(restaurant_id)
        user_uuid = uuid.UUID(user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format: {e}",
        )

    is_member = db.scalar(
        select(RestaurantUser.id).where(
            RestaurantUser.restaurant_id == restaurant_uuid,
            RestaurantUser.user_id == user_uuid,
            RestaurantUser.status != "removed",
        )
    )
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to this restaurant",
        )

    supplier = db.get(Suppliers, supplier_uuid)
    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    # Enforce user ownership. Legacy suppliers may have NULL user_id; claim them on first association.
    if supplier.user_id is None:
        supplier.user_id = user_uuid
    elif supplier.user_id != user_uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Supplier does not belong to this user",
        )

    if status_value not in {"active", "inactive"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 'active' or 'inactive'",
        )

    link = db.scalar(
        select(RestaurantSuppliers).where(
            RestaurantSuppliers.restaurant_id == restaurant_uuid,
            RestaurantSuppliers.supplier_id == supplier_uuid,
        )
    )
    if not link:
        link = RestaurantSuppliers(
            restaurant_id=restaurant_uuid,
            supplier_id=supplier_uuid,
            status=status_value,
            account_number=account_number,
            default_currency=default_currency,
            lead_time_days=lead_time_days,
            notes=notes,
        )
        db.add(link)
    else:
        link.status = status_value
        if account_number is not None:
            link.account_number = account_number
        if default_currency is not None:
            link.default_currency = default_currency
        if lead_time_days is not None:
            link.lead_time_days = lead_time_days
        if notes is not None:
            link.notes = notes

    db.commit()

    return {
        "status": "ok",
        "supplier_id": str(supplier_uuid),
        "restaurant_id": str(restaurant_uuid),
        "link": {
            "status": link.status,
            "account_number": link.account_number,
            "default_currency": link.default_currency,
            "lead_time_days": link.lead_time_days,
            "notes": link.notes,
        },
    }

