import hashlib
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.audit.service import record_audit_event
from app.modules.deliveries.models import Delivery
from app.modules.inventory.models import Inventory, Reservation
from app.modules.orders.models import Order, OrderItem, OrderRevision
from app.modules.orders.preview import ManualOrderPreview, preview_stock
from app.modules.orders.schemas import EditInput, OrderInput, OrderOut, PricedLine
from app.modules.products.bom_calculation import BomError
from app.modules.products.models import Bom, BomItem, Product


def read_order(db: Session, order_id: int, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    order = db.scalar(query)
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


def snapshot(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id, customer_code=order.customer_code, customer_po_number=order.customer_po_number,
        requested_delivery_date=order.requested_delivery_date, currency=order.currency,
        lines=[PricedLine.model_validate(item, from_attributes=True) for item in order.items],
        subtotal=sum((item.quantity * item.unit_price for item in order.items), Decimal(0)),
        status=order.status, stock=order.stock, revision=order.revision,
    )


def finish(db: Session, order: Order, actor_id: int, action: str, reason: str, *, commit: bool = True) -> OrderOut:
    db.flush()
    result = snapshot(order)
    data = result.model_dump(mode="json")
    db.add(OrderRevision(order_id=order.id, revision=order.revision, actor_id=actor_id,
                         snapshot=data, reason=reason))
    record_audit_event(db, actor_user_id=actor_id, action=action, entity_type="order",
                       entity_id=order.id, detail={"revision": order.revision, "reason": reason})
    if commit:
        db.commit()
    return result


def check_duplicates(db: Session, data: OrderInput, order_id: int | None) -> str:
    quantities = Counter()
    for line in data.lines:
        quantities[line.product_id] += line.quantity
    signature = hashlib.sha256(str(sorted(quantities.items())).encode()).hexdigest()
    # Serialize checks for a customer, including simultaneous submissions without a PO.
    db.execute(select(func.pg_advisory_xact_lock(501, func.hashtext(data.customer_code))))
    query = select(Order.id).where(Order.customer_code == data.customer_code)
    if order_id is not None:
        query = query.where(Order.id != order_id)
    if data.customer_po_number and db.scalar(query.where(Order.customer_po_number == data.customer_po_number)):
        raise HTTPException(409, "This customer already has an order with that PO number.")
    since = datetime.now(timezone.utc) - timedelta(days=settings.order_duplicate_days)
    similar = db.scalar(query.where(Order.line_signature == signature, Order.created_at >= since))
    if similar and not data.acknowledge_duplicate:
        raise HTTPException(409, f"Possible duplicate of order #{similar}. Acknowledge to continue.")
    return signature


def check_revision(order: Order, revision: int):
    if order.revision != revision:
        raise HTTPException(409, "This order has changed. Reload it before continuing.")


def allocate(db: Session, order: Order, actor_id: int):
    previous = db.scalars(select(Reservation).where(Reservation.order_id == order.id)).all()
    ids = {line.product_id for line in order.items}
    components = db.scalars(select(BomItem.component_product_id).join(Bom).where(
        Bom.product_id.in_(ids), Bom.status == "active"))
    inventory_ids = ids.union(components, (reservation.product_id for reservation in previous))
    # Competing confirmations lock shared balances in the same order.
    db.scalars(select(Inventory).where(Inventory.product_id.in_(inventory_ids))
               .order_by(Inventory.product_id).with_for_update()
               .execution_options(populate_existing=True)).all()
    for reservation in previous:
        db.execute(update(Inventory).where(Inventory.product_id == reservation.product_id)
                   .values(available=Inventory.available + reservation.quantity))
        db.delete(reservation)
    db.flush()
    lines = [{"product_id": item.product_id, "quantity": item.quantity} for item in order.items]
    try:
        plan = preview_stock(db, ManualOrderPreview(lines=lines))
    except BomError as exc:
        raise HTTPException(422, str(exc)) from exc
    for need in plan.finished_goods + plan.components:
        if need.from_stock:
            claimed = db.execute(update(Inventory).where(
                Inventory.product_id == need.product_id, Inventory.available >= need.from_stock,
            ).values(available=Inventory.available - need.from_stock))
            if claimed.rowcount != 1:
                raise HTTPException(409, "Available stock changed. Retry this order.")
            db.add(Reservation(order_id=order.id, product_id=need.product_id,
                               quantity=need.from_stock, user_id=actor_id))
    order.stock = plan.model_dump(mode="json")
    if any(item.shortfall for item in plan.components):
        order.status = "awaiting_materials"
    elif any(item.shortfall for item in plan.finished_goods):
        order.status = "ready_for_production"
    else:
        order.status = "ready_for_delivery"


def save_order(db: Session, data: OrderInput, actor_id: int, order: Order | None = None,
               *, commit: bool = True, reason: str = "Manual order created") -> OrderOut:
    editing = order is not None
    if isinstance(data, EditInput) and order is not None:
        check_revision(order, data.revision)
        if order.status in {"partially_dispatched", "dispatched", "delivered"}:
            raise HTTPException(409, "This order has shipped quantities and cannot be revised.")
        reason = data.reason
    ids = {line.product_id for line in data.lines}
    valid = set(db.scalars(select(Product.id).where(Product.id.in_(ids), Product.is_active,
                                                     Product.product_type == "FINISHED")))
    if ids != valid:
        raise HTTPException(422, "Each line must reference an active finished product.")
    signature = check_duplicates(db, data, order.id if order else None)
    if order is None:
        order = Order()
        db.add(order)
    else:
        db.execute(update(Delivery).where(Delivery.order_id == order.id, Delivery.revision == order.revision,
                                          Delivery.status.in_(["pending", "ready"])).values(status="superseded"))
        order.revision += 1
    order.customer_code = data.customer_code
    order.customer_po_number = data.customer_po_number
    order.requested_delivery_date = data.requested_delivery_date
    order.currency = data.currency
    order.line_signature = signature
    order.items = [OrderItem(**line.model_dump()) for line in data.lines]
    db.flush()
    if order.status != "draft":
        allocate(db, order, actor_id)
    return finish(db, order, actor_id, "order.edited" if editing else "order.created", reason, commit=commit)


def confirm_order(db: Session, order: Order, revision: int, actor_id: int, *, commit: bool = True,
                  reason: str = "Manual order confirmed") -> OrderOut:
    check_revision(order, revision)
    if order.status != "draft":
        raise HTTPException(409, "Only a draft order can be confirmed.")
    if order.requested_delivery_date and order.requested_delivery_date < date.today():
        raise HTTPException(422, "Update the delivery date before confirming.")
    allocate(db, order, actor_id)
    order.revision += 1
    return finish(db, order, actor_id, "order.confirmed", reason, commit=commit)
