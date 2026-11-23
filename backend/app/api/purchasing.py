from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.purchasing.models import PurchaseOrder, PurchaseRequisition, Supplier
from app.modules.purchasing.service import (
    approve_purchase_order, draft_purchase_order, draft_requisition, mark_purchase_order_sent, submit_purchase_order,
)

router = APIRouter(prefix="/purchasing", tags=["purchasing"])
Buyer = Annotated[User, Depends(require_permission("purchasing:manage"))]
Approver = Annotated[User, Depends(require_permission("purchasing:approve"))]
PurchaseReader = Annotated[User, Depends(require_permission("purchasing:read"))]
Database = Annotated[Session, Depends(get_db)]


class RequisitionLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    component_product_id: int
    quantity_needed: Decimal


class RequisitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    status: Literal["draft"]
    source_order_id: int
    source_revision: int
    created_by: int
    items: list[RequisitionLine]


@router.put("/orders/{order_id}/revisions/{revision}/requisition", response_model=RequisitionOut)
def create_requisition(order_id: int, revision: int, db: Database, actor: Buyer):
    return draft_requisition(db, order_id, revision, actor.id)


@router.get("/requisitions/{requisition_id}", response_model=RequisitionOut)
def get_requisition(requisition_id: int, db: Database, _: Buyer):
    draft = db.get(PurchaseRequisition, requisition_id)
    if draft is None:
        raise HTTPException(404, "Requisition not found.")
    return draft


class DraftPurchaseOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: int = Field(strict=True, gt=0, le=2**63 - 1)


class PurchaseLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: int
    quantity: Decimal
    unit_cost: Decimal


class PurchaseOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    po_number: str
    requisition_id: int
    supplier_id: int
    created_by: int
    submitted_by: int | None
    approved_by: int | None
    sent_by: int | None
    sent_at: datetime | None
    status: Literal["draft", "pending_approval", "approved", "sent"]
    currency: Literal["IDR"] = "IDR"
    total: Decimal
    items: list[PurchaseLine]


@router.get("/suppliers")
def list_suppliers(db: Database, _: Buyer):
    return db.execute(select(Supplier.id, Supplier.code, Supplier.name).order_by(Supplier.code)).mappings().all()


@router.put("/requisitions/{requisition_id}/purchase-order", response_model=PurchaseOrderOut)
def create_purchase_order(requisition_id: int, data: DraftPurchaseOrderInput, db: Database, actor: Buyer):
    return draft_purchase_order(db, requisition_id, data.supplier_id, actor.id)


@router.get("/purchase-orders/{purchase_order_id}", response_model=PurchaseOrderOut)
def get_purchase_order(purchase_order_id: int, db: Database, _: PurchaseReader):
    draft = db.get(PurchaseOrder, purchase_order_id)
    if draft is None:
        raise HTTPException(404, "Purchase order not found.")
    return draft


@router.post("/purchase-orders/{purchase_order_id}/submit", response_model=PurchaseOrderOut)
def submit(purchase_order_id: int, db: Database, actor: Buyer):
    return submit_purchase_order(db, purchase_order_id, actor.id)


@router.post("/purchase-orders/{purchase_order_id}/approve", response_model=PurchaseOrderOut)
def approve(purchase_order_id: int, db: Database, actor: Approver):
    return approve_purchase_order(db, purchase_order_id, actor.id)


@router.post("/purchase-orders/{purchase_order_id}/mark-sent", response_model=PurchaseOrderOut)
def mark_sent(purchase_order_id: int, db: Database, actor: Buyer):
    """Record that the buyer has already contacted the supplier."""
    return mark_purchase_order_sent(db, purchase_order_id, actor.id)
