from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.modules.inventory.models import Inventory
from app.modules.products.bom_calculation import BomError, ProductNotFoundError
from app.modules.products.bom_service import calculate_product_requirements
from app.modules.products.models import Product
from app.modules.products.schemas import CalculationRequest


class OrderLine(CalculationRequest):
    product_id: int = Field(strict=True, gt=0, le=2**63 - 1)


class ManualOrderPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[OrderLine] = Field(min_length=1, max_length=100)


class StockNeed(BaseModel):
    product_id: int
    required: Decimal
    from_stock: Decimal
    shortfall: Decimal


class StockPreview(BaseModel):
    finished_goods: list[StockNeed]
    components: list[StockNeed]


def stock_need(db: Session, product_id: int, required: Decimal) -> StockNeed:
    balance = db.get(Inventory, product_id)
    covered = min(required, balance.available) if balance else Decimal(0)
    return StockNeed(product_id=product_id, required=required, from_stock=covered,
                     shortfall=required - covered)


def preview_stock(db: Session, order: ManualOrderPreview) -> StockPreview:
    requested = defaultdict(Decimal)
    for line in order.lines:
        requested[line.product_id] += line.quantity

    finished = []
    components = defaultdict(Decimal)
    for product_id, quantity in requested.items():
        product = db.get(Product, product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} does not exist.")
        if not product.is_active or product.product_type != "FINISHED":
            raise BomError(f"Product '{product.sku}' must be an active finished good.")
        need = stock_need(db, product_id, quantity)
        finished.append(need)
        if need.shortfall:
            for item in calculate_product_requirements(db, product_id, need.shortfall):
                components[item.component_product_id] += item.required

    return StockPreview(finished_goods=finished, components=[
        stock_need(db, product_id, quantity) for product_id, quantity in components.items()
    ])
