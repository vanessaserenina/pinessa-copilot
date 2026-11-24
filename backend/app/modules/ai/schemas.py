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


class MatchCandidate(BaseModel):
    product_id: int
    sku: str
    name: str
    unit: str
    attributes: dict
    method: Literal["sku", "alias", "text"]
    score: float


class SourceQuantity(BaseModel):
    quantity: str
    unit: str
    normalized_unit: str


class LineMatch(BaseModel):
    source_quantity: SourceQuantity | None = None
    line_index: int
    requested_text: str
    normalized_text: str
    normalized_unit: str | None
    selected_product_id: int | None
    method: Literal["sku", "alias", "text"] | None
    status: Literal["auto", "needs_review"]
    reason: str | None
    candidates: list[MatchCandidate]


class ReviewHeaders(BaseModel):
    customer_name: str | None
    po_number: str | None
    check_po: bool = False


class ExtractionOut(BaseModel):
    id: int
    order_source_id: int
    model_name: str
    prompt_version: str
    schema_valid: bool
    status: Literal["needs_review", "needs_manual_handling", "confirmed"]
    validated_output: StructuredOrder | None
    review_headers: ReviewHeaders | None = None
    error_message: str | None
    line_matches: list[LineMatch] | None
    confirmed_order_id: int | None
