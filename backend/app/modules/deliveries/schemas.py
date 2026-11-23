from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

DocumentType = Literal["delivery_note", "invoice"]
NextStatus = Literal["ready", "dispatched", "delivered"]


class ShipmentLine(BaseModel):
    line_no: int = Field(strict=True, gt=0)
    quantity: int = Field(strict=True, gt=0)


class PlanDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(gt=0)
    delivery_number: str = Field(min_length=1, max_length=80)
    address_snapshot: str = Field(min_length=1, max_length=500)
    planned_date: date | None = None
    items: list[ShipmentLine] = Field(min_length=1, max_length=100)


class DocumentLink(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document_type: DocumentType
    file_name: str = Field(min_length=1, max_length=255)
    url: HttpUrl = Field(max_length=2048)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    document_type: DocumentType
    file_name: str
    url: str | None
    sha256: str | None
    mime_type: str | None
    uploaded_by: int


class LineOut(ShipmentLine):
    model_config = ConfigDict(from_attributes=True)
    product_id: int


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    revision: int
    delivery_number: str
    address_snapshot: str
    planned_date: date | None
    status: str
    dispatched_at: datetime | None
    delivered_at: datetime | None
    items: list[LineOut]
    documents: list[DocumentOut]
