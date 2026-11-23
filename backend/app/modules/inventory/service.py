from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.inventory.models import Inventory
from app.modules.products.bom_calculation import WHOLE_NUMBER_UNITS
from app.modules.products.models import Product


def validate_quantity(product: Product, quantity: Decimal) -> None:
    if product.unit_of_measure in WHOLE_NUMBER_UNITS and quantity != quantity.to_integral_value():
        raise HTTPException(422, f"Quantity must be a whole number for {product.unit_of_measure}.")


def add_stock(db: Session, product_id: int, quantity: Decimal) -> Decimal:
    stmt = insert(Inventory).values(product_id=product_id, available=quantity)
    return db.execute(stmt.on_conflict_do_update(
        index_elements=[Inventory.product_id],
        set_={"available": Inventory.available + quantity},
    ).returning(Inventory.available)).scalar_one()
