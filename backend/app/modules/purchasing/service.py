from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.service import record_audit_event
from app.modules.inventory.service import add_stock, validate_quantity
from app.modules.orders.preview import StockPreview
from app.modules.orders.service import check_revision, read_order
from app.modules.products.models import Product
from app.modules.purchasing.models import (
    GoodsReceiptLine, PurchaseOrder, PurchaseOrderItem, PurchaseRequisition, PurchaseRequisitionItem, Supplier,
    SupplierProduct,
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


def current_purchase_order(db: Session, purchase_order_id: int) -> PurchaseOrder:
    requisition = db.scalar(select(PurchaseRequisition).join(PurchaseOrder).where(
        PurchaseOrder.id == purchase_order_id,
    ))
    if requisition is None:
        raise HTTPException(404, "Purchase order not found.")
    # All pre-dispatch transitions and replacements lock the source order first.
    source = read_order(db, requisition.source_order_id, lock=True)
    if source.revision != requisition.source_revision:
        raise HTTPException(409, "The source order was revised. Use its current requisition to replace this PO.")
    return locked_purchase_order(db, purchase_order_id)


def submit_purchase_order(db: Session, purchase_order_id: int, actor_id: int) -> PurchaseOrder:
    po = current_purchase_order(db, purchase_order_id)
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
    po = current_purchase_order(db, purchase_order_id)
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


def mark_purchase_order_sent(db: Session, purchase_order_id: int, actor_id: int) -> PurchaseOrder:
    po = current_purchase_order(db, purchase_order_id)
    if po.status != "approved":
        raise HTTPException(409, "Only an approved purchase order can be marked as sent.")
    po.sent_by = actor_id
    po.sent_at = datetime.now(timezone.utc)
    po.status = "sent"
    record_audit_event(db, action="purchase_order.sent", entity_type="purchase_order",
                       actor_user_id=actor_id, entity_id=po.id, before={"status": "approved"},
                       after={"status": po.status, "sent_at": po.sent_at})
    db.commit()
    return po


def receive_purchase_order(db: Session, purchase_order_id: int, receipt_id: UUID,
                           quantities: dict[int, Decimal], actor_id: int) -> PurchaseOrder:
    po = locked_purchase_order(db, purchase_order_id)
    recorded = dict(db.execute(select(GoodsReceiptLine.product_id, GoodsReceiptLine.quantity).where(
        GoodsReceiptLine.purchase_order_id == po.id, GoodsReceiptLine.receipt_id == receipt_id,
    )).all())
    if recorded:
        if recorded != quantities:
            raise HTTPException(409, "This receipt ID already records a different delivery.")
        return po
    if po.status not in ("sent", "partially_received"):
        raise HTTPException(409, "Only a sent purchase order with outstanding quantities can receive goods.")

    items = {line.product_id: line for line in po.items}
    for product_id, quantity in quantities.items():
        line = items.get(product_id)
        if line is None:
            raise HTTPException(422, f"Product {product_id} is not on this purchase order.")
        validate_quantity(db.get_one(Product, product_id), quantity)
        if quantity > line.quantity - line.quantity_received:
            raise HTTPException(409, f"Receipt exceeds the outstanding quantity for product {product_id}.")

    # Lock shared inventory balances in the same order across concurrent receipts.
    for product_id, quantity in sorted(quantities.items()):
        add_stock(db, product_id, quantity)
        items[product_id].quantity_received += quantity
        db.add(GoodsReceiptLine(purchase_order_id=po.id, receipt_id=receipt_id, product_id=product_id,
                                quantity=quantity, received_by=actor_id))
    previous_status = po.status
    complete = all(line.quantity_received == line.quantity for line in po.items)
    po.status = "received" if complete else "partially_received"
    record_audit_event(db, action="purchase_order.goods_received", entity_type="purchase_order",
                       actor_user_id=actor_id, entity_id=po.id, before={"status": previous_status},
                       after={"status": po.status}, detail={"receipt_id": str(receipt_id)})
    db.commit()
    return po


def draft_purchase_order(db: Session, requisition_id: int, supplier_id: int, actor_id: int) -> PurchaseOrder:
    requisition = db.get(PurchaseRequisition, requisition_id)
    if requisition is None:
        raise HTTPException(404, "Requisition not found.")
    # Serialize conversion with order revisions and competing buyers.
    source = read_order(db, requisition.source_order_id, lock=True)
    check_revision(source, requisition.source_revision)
    existing = db.scalar(select(PurchaseOrder).join(PurchaseRequisition).where(
        PurchaseRequisition.source_order_id == source.id, PurchaseOrder.status != "superseded",
    ).with_for_update(of=PurchaseOrder).execution_options(populate_existing=True))
    if existing is not None:
        if existing.requisition_id == requisition_id and existing.supplier_id == supplier_id:
            return existing
        if existing.requisition_id == requisition_id or existing.sent_by is not None:
            raise HTTPException(409, f"Review existing purchase order {existing.po_number} for this order.")
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
    if existing is not None:
        previous_status = existing.status
        existing.status = "superseded"
        record_audit_event(db, action="purchase_order.superseded", entity_type="purchase_order",
                           actor_user_id=actor_id, entity_id=existing.id, before={"status": previous_status},
                           after={"status": existing.status}, detail={"replacement_po_id": draft.id})
    db.commit()
    return draft
