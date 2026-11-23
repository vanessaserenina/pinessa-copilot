from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.purchasing.models import PurchaseRequisition
from app.modules.purchasing.service import draft_requisition

router = APIRouter(prefix="/purchasing", tags=["purchasing"])
Buyer = Annotated[User, Depends(require_permission("purchasing:manage"))]
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
