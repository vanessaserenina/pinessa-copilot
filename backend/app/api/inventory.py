from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.inventory.models import QUANTITY_PRECISION, QUANTITY_SCALE, Inventory, Reservation
from app.modules.products.bom_calculation import WHOLE_NUMBER_UNITS
from app.modules.products.models import Product

router = APIRouter(prefix="/inventory", tags=["inventory"])


class Units(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=QUANTITY_PRECISION, decimal_places=QUANTITY_SCALE)


class BalanceOut(BaseModel):
    product_id: int
    available: Decimal


class ReservationOut(BaseModel):
    id: int
    product_id: int
    quantity: Decimal


def get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found.")
    return product


def validate_quantity(product: Product, quantity: Decimal) -> None:
    if product.unit_of_measure in WHOLE_NUMBER_UNITS and quantity != quantity.to_integral_value():
        raise HTTPException(422, f"Quantity must be a whole number for {product.unit_of_measure}.")


@router.get("/{product_id}", response_model=BalanceOut)
def balance(product_id: int, db: Session = Depends(get_db),
            _: User = Depends(require_permission("products:read"))):
    get_product(db, product_id)
    stock = db.get(Inventory, product_id)
    return {"product_id": product_id, "available": stock.available if stock else 0}


@router.post("/{product_id}/receipts", response_model=BalanceOut)
def receive(product_id: int, units: Units, db: Session = Depends(get_db),
            _: User = Depends(require_permission("inventory:receive"))):
    validate_quantity(get_product(db, product_id), units.quantity)
    stmt = insert(Inventory).values(product_id=product_id, available=units.quantity)
    available = db.scalar(stmt.on_conflict_do_update(
        index_elements=[Inventory.product_id],
        set_={"available": Inventory.available + units.quantity},
    ).returning(Inventory.available))
    db.commit()
    return {"product_id": product_id, "available": available}


@router.post("/{product_id}/reservations", status_code=201, response_model=ReservationOut)
def reserve(product_id: int, units: Units, db: Session = Depends(get_db),
            actor: User = Depends(require_permission("inventory:reserve"))):
    validate_quantity(get_product(db, product_id), units.quantity)
    # PostgreSQL rechecks this predicate after a competing writer commits.
    claimed = db.scalar(update(Inventory).where(
        Inventory.product_id == product_id, Inventory.available >= units.quantity,
    ).values(available=Inventory.available - units.quantity).returning(Inventory.product_id))
    if claimed is None:
        raise HTTPException(409, "Insufficient available stock.")
    reservation = Reservation(product_id=product_id, quantity=units.quantity, user_id=actor.id)
    db.add(reservation)
    db.commit()
    return {"id": reservation.id, "product_id": product_id, "quantity": units.quantity}
