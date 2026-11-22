from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---- BOMs -----------------------------------------------------------------
class BomItemOut(BaseModel):
    component_product_id: int
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity_per_unit: Decimal
    scrap_percent: Decimal


class BomSummary(BaseModel):
    id: int
    version_number: int
    status: str
    effective_from: date | None
    is_synthetic: bool
    line_count: int
    created_at: datetime


class BomOut(BaseModel):
    id: int
    product_id: int
    product_sku: str
    version_number: int
    status: str
    effective_from: date | None
    is_synthetic: bool
    notes: str | None
    created_at: datetime
    items: list[BomItemOut]

    @classmethod
    def from_bom(cls, bom) -> "BomOut":
        return cls(
            id=bom.id,
            product_id=bom.product_id,
            product_sku=bom.product.sku,
            version_number=bom.version_number,
            status=bom.status,
            effective_from=bom.effective_from,
            is_synthetic=bom.is_synthetic,
            notes=bom.notes,
            created_at=bom.created_at,
            items=[
                BomItemOut(
                    component_product_id=item.component_product_id,
                    component_sku=item.component.sku,
                    component_name=item.component.name,
                    unit_of_measure=item.component.unit_of_measure,
                    quantity_per_unit=item.quantity_per_unit,
                    scrap_percent=item.scrap_percent,
                )
                for item in bom.items
            ],
        )


class BomItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_product_id: int
    quantity_per_unit: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    scrap_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)


class BomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)
    items: list[BomItemIn] = Field(min_length=1, max_length=100)


# ---- Products -------------------------------------------------------------
class ProductListItem(BaseModel):
    id: int
    sku: str
    name: str
    product_type: str
    unit_of_measure: str
    source: str
    is_active: bool
    attributes: dict[str, Any]
    family_code: str
    family_name: str
    category_code: str
    category_name: str
    section_code: str
    section_name: str
    is_custom_allowed: bool
    has_active_bom: bool


class ProductPage(BaseModel):
    items: list[ProductListItem]
    total: int
    limit: int
    offset: int


class ProductDetail(ProductListItem):
    option_groups: dict[str, Any]
    boms: list[BomSummary]
    active_bom: BomOut | None
    used_in_active_boms: int


# ---- Calculation ----------------------------------------------------------
class CalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(strict=True, gt=0, le=10_000_000)


class CalculationLine(BaseModel):
    component_product_id: int
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity_per_unit: Decimal
    scrap_percent: Decimal
    exact_required: Decimal
    required: Decimal


class CalculationOut(BaseModel):
    product_id: int
    product_sku: str
    product_name: str
    bom_id: int
    bom_version: int
    quantity: int
    lines: list[CalculationLine]
