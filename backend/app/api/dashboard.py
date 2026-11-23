from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.ai.models import AIExtraction
from app.modules.audit.models import AuditEvent, NotificationRead
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS
from app.modules.deliveries.models import Delivery
from app.modules.orders.models import Order, OrderSource
from app.modules.production.models import ProductionJob
from app.modules.purchasing.models import PurchaseOrder, PurchaseRequisition

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


def pending_orders():
    return select(PurchaseOrder.id).join(PurchaseRequisition).join(
        Order, Order.id == PurchaseRequisition.source_order_id).where(
        PurchaseOrder.status == "pending_approval", Order.revision == PurchaseRequisition.source_revision)


def notifications(actor: User):
    permissions = ROLE_PERMISSIONS.get(actor.role.name, frozenset())
    conditions = []
    if "orders:manage" in permissions:
        conditions.extend([
            and_(AuditEvent.action == "ai_extraction.needs_manual_handling",
                 AuditEvent.entity_id.in_(select(AIExtraction.id).where(AIExtraction.status == "needs_manual_handling"))),
            and_(AuditEvent.action == "order_source.failed_extraction",
                 AuditEvent.entity_id.in_(select(OrderSource.id).where(OrderSource.status == "failed_extraction"))),
        ])
    if "purchasing:approve" in permissions:
        eligible = pending_orders().where(PurchaseOrder.created_by != actor.id, PurchaseOrder.submitted_by != actor.id)
        conditions.append(and_(AuditEvent.action == "purchase_order.submitted", AuditEvent.entity_id.in_(eligible)))
    if permissions & {"orders:manage", "production:manage", "purchasing:manage"}:
        shortage = select(Order.id).where(Order.status == "awaiting_materials",
                                          Order.revision == AuditEvent.detail["revision"].as_integer())
        conditions.append(and_(AuditEvent.action.in_(["order.confirmed", "order.edited"]),
                               AuditEvent.entity_id.in_(shortage)))
    return select(AuditEvent).where(or_(false(), *conditions))


@router.get("")
def overview(actor: Actor, db: Database):
    permissions = ROLE_PERMISSIONS.get(actor.role.name, frozenset())
    groups = {}
    if "orders:manage" in permissions:
        groups["Orders"] = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    if permissions & {"purchasing:manage", "production:manage", "orders:manage"}:
        groups["Material shortages"] = {"orders awaiting materials": db.scalar(
            select(func.count()).select_from(Order).where(Order.status == "awaiting_materials"))}
    if "purchasing:read" in permissions:
        groups["Purchasing"] = {"pending approval": db.scalar(select(func.count()).select_from(pending_orders().subquery()))}
    if "production:manage" in permissions:
        status = case((ProductionJob.completed_at.is_not(None), "completed"),
                      (ProductionJob.started_at.is_not(None), "in progress"), else_="planned")
        groups["Production jobs"] = dict(db.execute(select(status, func.count()).select_from(ProductionJob).join(
            Order, Order.id == ProductionJob.order_id).where(
            Order.revision == ProductionJob.revision).group_by(status)).all())
    if "deliveries:manage" in permissions:
        groups["Deliveries"] = dict(db.execute(select(Delivery.status, func.count()).group_by(Delivery.status)).all())

    unread = notifications(actor).where(~select(NotificationRead.event_id).where(
        NotificationRead.user_id == actor.id, NotificationRead.event_id == AuditEvent.id).exists())
    messages = {"ai_extraction.needs_manual_handling": "Proposal requires manual handling",
                "order_source.failed_extraction": "Source text extraction failed",
                "purchase_order.submitted": "Purchase order awaits your approval",
                "order.edited": "Order is awaiting materials", "order.confirmed": "Order is awaiting materials"}
    alerts = [{"id": event.id, "message": messages[event.action],
               "entity": event.entity_type, "entity_id": event.entity_id}
              for event in db.scalars(unread.order_by(AuditEvent.id.desc()).limit(50))]
    return {"groups": groups, "notifications": alerts}


@router.post("/notifications/{event_id}/read", status_code=204)
def mark_read(event_id: int, actor: Actor, db: Database):
    if db.scalar(notifications(actor).where(AuditEvent.id == event_id)) is None:
        raise HTTPException(404, "Notification not found.")
    db.execute(insert(NotificationRead).values(user_id=actor.id, event_id=event_id).on_conflict_do_nothing())
    db.commit()
