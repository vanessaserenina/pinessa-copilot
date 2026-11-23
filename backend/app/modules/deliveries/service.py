from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.audit.service import record_audit_event
from app.modules.deliveries.models import Delivery, DeliveryItem
from app.modules.deliveries.schemas import NextStatus, PlanDelivery
from app.modules.inventory.models import Reservation
from app.modules.orders.service import check_revision, read_order


def read_delivery(db: Session, delivery_id: int) -> Delivery:
    delivery = db.get(Delivery, delivery_id)
    if delivery is None:
        raise HTTPException(404, "Delivery not found.")
    return delivery


def plan_delivery(db: Session, order_id: int, data: PlanDelivery, actor_id: int) -> Delivery:
    order = read_order(db, order_id, lock=True)
    check_revision(order, data.revision)
    if order.status not in {"ready_for_delivery", "partially_dispatched"}:
        raise HTTPException(409, "Delivery requires an order with finished goods ready to ship.")
    allocated = dict(db.execute(select(DeliveryItem.line_no, func.sum(DeliveryItem.quantity))
        .join(Delivery).where(Delivery.order_id == order_id, Delivery.revision == order.revision)
        .group_by(DeliveryItem.line_no)).all())
    items = []
    seen = set()
    for line in data.items:
        if line.line_no > len(order.items) or line.line_no in seen:
            raise HTTPException(422, "Specify each order line once using its one-based line number.")
        seen.add(line.line_no)
        item = order.items[line.line_no - 1]
        if line.quantity + allocated.get(line.line_no, 0) > item.quantity:
            raise HTTPException(409, f"Delivery quantity exceeds the remaining quantity on line {line.line_no}.")
        items.append(DeliveryItem(line_no=line.line_no, order_item_id=item.id,
                                  product_id=item.product_id, quantity=line.quantity))
    delivery_id = db.scalar(insert(Delivery).values(order_id=order_id, **data.model_dump(exclude={"items"}))
                            .on_conflict_do_nothing(index_elements=["delivery_number"]).returning(Delivery.id))
    if delivery_id is None:
        raise HTTPException(409, "This delivery number is already in use.")
    delivery = read_delivery(db, delivery_id)
    delivery.items = items
    record_audit_event(db, actor_user_id=actor_id, action="delivery.created", entity_type="delivery",
                       entity_id=delivery_id, detail={"order_id": order_id, "revision": order.revision})
    db.commit()
    return delivery


def issue_finished_goods(db: Session, delivery: Delivery):
    required = Counter()
    for item in delivery.items:
        required[item.product_id] += item.quantity
    reservations = db.scalars(select(Reservation).where(
        Reservation.order_id == delivery.order_id, Reservation.product_id.in_(required)
    ).order_by(Reservation.id).with_for_update().execution_options(populate_existing=True))
    for row in reservations:
        used = min(required[row.product_id], row.quantity)
        required[row.product_id] -= used
        if used == row.quantity:
            db.delete(row)
        elif used:
            row.quantity -= used
    if any(required.values()):
        raise HTTPException(409, "The order has insufficient reserved finished goods for this dispatch.")


def set_delivery_status(db: Session, delivery_id: int, status: NextStatus, actor_id: int) -> Delivery:
    delivery = read_delivery(db, delivery_id)
    order = read_order(db, delivery.order_id, lock=True)
    db.refresh(delivery)
    check_revision(order, delivery.revision)
    if status == delivery.status:
        return delivery
    previous = {"ready": "pending", "dispatched": "ready", "delivered": "dispatched"}
    if delivery.status != previous[status]:
        raise HTTPException(409, f"Only a {previous[status]} delivery can be marked {status}.")
    delivery.status = status
    if status == "dispatched":
        issue_finished_goods(db, delivery)
        delivery.dispatched_at = datetime.now(timezone.utc)
    if status == "delivered":
        delivery.delivered_at = datetime.now(timezone.utc)
    if status in {"dispatched", "delivered"}:
        db.flush()
        shipped = dict(db.execute(select(Delivery.status, func.sum(DeliveryItem.quantity))
            .join(DeliveryItem).where(Delivery.order_id == order.id, Delivery.revision == order.revision,
                                      Delivery.status.in_(["dispatched", "delivered"]))
            .group_by(Delivery.status)).all())
        ordered = sum(item.quantity for item in order.items)
        if shipped.get("delivered", 0) == ordered:
            order.status = "delivered"
        elif sum(shipped.values()) == ordered:
            order.status = "dispatched"
        else:
            order.status = "partially_dispatched"
    record_audit_event(db, actor_user_id=actor_id, action=f"delivery.{status}", entity_type="delivery",
                       entity_id=delivery.id, detail={"order_id": order.id})
    db.commit()
    return delivery
