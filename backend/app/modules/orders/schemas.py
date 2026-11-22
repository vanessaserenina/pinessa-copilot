from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.orders.preview import OrderLine, StockPreview


class PricedLine(OrderLine):
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=4)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_code: str = Field(min_length=1, max_length=80)
    customer_po_number: str | None = Field(default=None, max_length=120)
    requested_delivery_date: date | None = None
    currency: str = Field(pattern="^[A-Z]{3}$")
    lines: list[PricedLine] = Field(min_length=1, max_length=100)
    acknowledge_duplicate: bool = False

    @field_validator("customer_code", "customer_po_number", "currency", mode="before")
    @classmethod
    def normalize_code(cls, value):
        if isinstance(value, str):
            return value.strip().upper() or None
        return value

    @field_validator("requested_delivery_date")
    @classmethod
    def validate_delivery(cls, value):
        if value and value < date.today():
            raise ValueError("Delivery date must be today or later.")
        return value


class EditInput(OrderInput):
    revision: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0)


class OrderOut(BaseModel):
    id: int
    customer_code: str
    customer_po_number: str | None
    requested_delivery_date: date | None
    currency: str
    lines: list[PricedLine]
    subtotal: Decimal
    status: str
    revision: int
    stock: StockPreview | None
