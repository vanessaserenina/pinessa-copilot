from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.service import record_audit_event
from app.modules.orders.preview import StockPreview
from app.modules.orders.service import check_revision, read_order
from app.modules.purchasing.models import PurchaseRequisition, PurchaseRequisitionItem


def draft_requisition(db: Session, order_id: int, revision: int, actor_id: int) -> PurchaseRequisition:
    order = read_order(db, order_id, lock=True)
    check_revision(order, revision)
    existing = db.scalar(select(PurchaseRequisition).where(
        PurchaseRequisition.source_order_id == order.id,
        PurchaseRequisition.source_revision == revision,
    ))
    if existing is not None:
        return existing
    if order.status != "awaiting_materials":
        raise HTTPException(409, "A confirmed order with component shortages is required.")

    stock = StockPreview.model_validate(order.stock)
    draft = PurchaseRequisition(
        source_order_id=order.id, source_revision=revision, created_by=actor_id,
        items=[PurchaseRequisitionItem(component_product_id=row.product_id, quantity_needed=row.shortfall)
               for row in stock.components if row.shortfall > 0],
    )
    db.add(draft)
    db.flush()
    record_audit_event(db, action="requisition.created", entity_type="purchase_requisition",
                       actor_user_id=actor_id, entity_id=draft.id,
                       detail={"order_id": order.id, "revision": revision})
    db.commit()
    return draft
