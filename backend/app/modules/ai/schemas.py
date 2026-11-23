from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FixedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Customer(FixedSchema):
    name: str | None
    code: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    delivery_address: str | None


class RequestedLine(FixedSchema):
    requested_text: Annotated[str, Field(min_length=1)]
    stated_sku: str | None
    # Decimal text preserves fractional units without binary floating-point rounding.
    quantity: Annotated[str, Field(pattern=r"^-?[0-9]+(\.[0-9]+)?$")] | None
    unit: str | None
    specification: str | None
    customization_notes: str | None


class StructuredOrder(FixedSchema):
    customer: Customer | None
    customer_po_number: str | None
    requested_delivery_date: date | None
    lines: list[RequestedLine]


class ExtractionOut(BaseModel):
    id: int
    order_source_id: int
    model_name: str
    prompt_version: str
    schema_valid: bool
    status: Literal["needs_review", "needs_manual_handling"]
    validated_output: StructuredOrder | None
    error_message: str | None
