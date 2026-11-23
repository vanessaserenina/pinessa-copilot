from decimal import Decimal, ROUND_HALF_UP, localcontext

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.service import record_audit_event
from app.modules.orders.preview import StockPreview
from app.modules.orders.service import check_revision, read_order
from app.modules.purchasing.models import (
    PurchaseOrder, PurchaseOrderItem, PurchaseRequisition, PurchaseRequisitionItem, Supplier, SupplierProduct,
)


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


def locked_purchase_order(db: Session, purchase_order_id: int) -> PurchaseOrder:
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == purchase_order_id)
                   .with_for_update().execution_options(populate_existing=True))
    if po is None:
        raise HTTPException(404, "Purchase order not found.")
    return po


def submit_purchase_order(db: Session, purchase_order_id: int, actor_id: int) -> PurchaseOrder:
    po = locked_purchase_order(db, purchase_order_id)
    if po.status != "draft":
        raise HTTPException(409, "Only a draft purchase order can be submitted.")
    po.submitted_by = actor_id
    po.status = "pending_approval"
    record_audit_event(db, action="purchase_order.submitted", entity_type="purchase_order",
                       actor_user_id=actor_id, entity_id=po.id, before={"status": "draft"},
                       after={"status": po.status})
    db.commit()
    return po


def approve_purchase_order(db: Session, purchase_order_id: int, actor_id: int) -> PurchaseOrder:
    po = locked_purchase_order(db, purchase_order_id)
    if po.status != "pending_approval":
        raise HTTPException(409, "Only a purchase order pending approval can be approved.")
    if actor_id in (po.created_by, po.submitted_by):
        raise HTTPException(403, "A different user must approve this purchase order.")
    po.approved_by = actor_id
    po.status = "approved"
    record_audit_event(db, action="purchase_order.approved", entity_type="purchase_order",
                       actor_user_id=actor_id, entity_id=po.id, before={"status": "pending_approval"},
                       after={"status": po.status})
    db.commit()
    return po


def draft_purchase_order(db: Session, requisition_id: int, supplier_id: int, actor_id: int) -> PurchaseOrder:
    requisition = db.get(PurchaseRequisition, requisition_id)
    if requisition is None:
        raise HTTPException(404, "Requisition not found.")
    # Serialize conversion with order revisions and competing buyers.
    source = read_order(db, requisition.source_order_id, lock=True)
    existing = db.scalar(select(PurchaseOrder).join(PurchaseRequisition).where(
        PurchaseRequisition.source_order_id == source.id,
    ))
    if existing is not None:
        if existing.requisition_id == requisition_id and existing.supplier_id == supplier_id:
            return existing
        raise HTTPException(409, f"Review existing purchase order {existing.po_number} for this order.")
    check_revision(source, requisition.source_revision)
    if db.get(Supplier, supplier_id) is None:
        raise HTTPException(404, "Supplier not found.")

    required = {item.component_product_id: item.quantity_needed for item in requisition.items}
    prices = dict(db.execute(select(SupplierProduct.component_product_id, SupplierProduct.unit_cost).where(
        SupplierProduct.supplier_id == supplier_id, SupplierProduct.component_product_id.in_(required),
    )).all())
    missing = required.keys() - prices.keys()
    if missing:
        raise HTTPException(409, f"Supplier has no price for component IDs {sorted(missing)}.")
    lines = [PurchaseOrderItem(product_id=product_id, quantity=qty, unit_cost=prices[product_id])
             for product_id, qty in sorted(required.items())]
    # Extend fractional quantities exactly, then round the order total once to whole IDR.
    with localcontext() as context:
        context.prec = 60
        total = sum((line.quantity * line.unit_cost for line in lines), Decimal(0))
        total = total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    draft = PurchaseOrder(requisition_id=requisition_id, supplier_id=supplier_id, created_by=actor_id,
                          total=total, items=lines)
    db.add(draft)
    db.flush()
    record_audit_event(db, action="purchase_order.created", entity_type="purchase_order",
                       actor_user_id=actor_id, entity_id=draft.id,
                       detail={"requisition_id": requisition_id, "supplier_id": supplier_id, "total_idr": total})
    db.commit()
    return draft
