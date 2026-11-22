"""Loads a product's active BOM from the database and runs the calculation."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.products.bom_calculation import (
    BomLine,
    NoActiveBomError,
    ProductNotFoundError,
    RequirementLine,
    calculate_requirements,
)
from app.modules.products.models import Bom, BomItem, Product


def get_active_bom(db: Session, product_id: int) -> Bom | None:
    return db.scalar(
        select(Bom)
        .options(selectinload(Bom.items).selectinload(BomItem.component))
        .where(Bom.product_id == product_id, Bom.status == "active")
    )


def bom_lines(bom: Bom) -> list[BomLine]:
    return [
        BomLine(
            component_product_id=item.component_product_id,
            component_sku=item.component.sku,
            component_name=item.component.name,
            unit_of_measure=item.component.unit_of_measure,
            quantity_per_unit=item.quantity_per_unit,
            scrap_percent=item.scrap_percent,
        )
        for item in bom.items
    ]


def calculate_with_bom(
    db: Session, product_id: int, quantity: int | str | Decimal
) -> tuple[Bom, list[RequirementLine]]:
    """Return the active BOM that was used, together with the calculated requirements."""
    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError(f"Product {product_id} does not exist.")
    bom = get_active_bom(db, product_id)
    if bom is None:
        raise NoActiveBomError(
            f"Product '{product.sku}' has no active BOM. "
            "Either it is bought finished (check finished-goods stock only), or its BOM is missing."
        )
    return bom, calculate_requirements(quantity, bom_lines(bom))


def calculate_product_requirements(
    db: Session, product_id: int, quantity: int | str | Decimal
) -> list[RequirementLine]:
    return calculate_with_bom(db, product_id, quantity)[1]