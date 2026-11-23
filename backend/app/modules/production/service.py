from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.audit.service import record_audit_event
from app.modules.inventory.models import Inventory, Reservation
from app.modules.inventory.service import add_stock, validate_quantity
from app.modules.orders.preview import StockPreview
from app.modules.orders.service import check_revision, read_order
from app.modules.products.bom_calculation import BomError
from app.modules.products.bom_service import calculate_with_bom
from app.modules.products.models import Product
from app.modules.production.models import JobComponent, ProductionJob


def jobs_for_revision(db: Session, order_id: int, revision: int) -> list[ProductionJob]:
    return list(db.scalars(select(ProductionJob).where(
        ProductionJob.order_id == order_id, ProductionJob.revision == revision,
    ).options(selectinload(ProductionJob.components)).order_by(ProductionJob.product_id)))


def plan_jobs(db: Session, order_id: int, revision: int, actor_id: int) -> list[ProductionJob]:
    order = read_order(db, order_id, lock=True)
    check_revision(order, revision)
    if order.status == "draft":
        raise HTTPException(409, "Confirm the order before planning production.")
    existing = jobs_for_revision(db, order_id, revision)
    if existing:
        return existing

    stock = StockPreview.model_validate(order.stock)
    totals = defaultdict(Decimal)
    jobs = []
    for need in stock.finished_goods:
        if not need.shortfall:
            continue
        try:
            bom, requirements = calculate_with_bom(db, need.product_id, need.shortfall)
        except BomError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = ProductionJob(order_id=order_id, revision=revision, product_id=need.product_id,
                            bom_id=bom.id, quantity_planned=need.shortfall)
        for line in requirements:
            totals[line.component_product_id] += line.required
            job.components.append(JobComponent(component_product_id=line.component_product_id,
                                               quantity_required=line.required))
        jobs.append(job)

    if totals != {line.product_id: line.required for line in stock.components}:
        raise HTTPException(409, "BOM requirements changed. Revise the order to update its stock plan.")

    db.add_all(jobs)
    db.flush()
    for job in jobs:
        record_audit_event(db, actor_user_id=actor_id, action="production_job.planned",
                           entity_type="production_job", entity_id=job.id,
                           detail={"order_id": order_id, "revision": revision, "bom_id": job.bom_id})
    db.commit()
    return jobs_for_revision(db, order_id, revision)


def locked_job(db: Session, job_id: int):
    order_id = db.scalar(select(ProductionJob.order_id).where(ProductionJob.id == job_id))
    if order_id is None:
        raise HTTPException(404, "Production job not found.")
    # Serialize job updates with order edits and other jobs using this order's reservations.
    order = read_order(db, order_id, lock=True)
    job = db.get(ProductionJob, job_id, populate_existing=True)
    return order, job


def record_progress(db: Session, job_id: int, quantity: Decimal, actor_id: int) -> ProductionJob:
    order, job = locked_job(db, job_id)
    check_revision(order, job.revision)
    validate_quantity(db.get(Product, job.product_id), quantity)
    if job.completed_at or quantity < job.quantity_completed:
        raise HTTPException(409, "Progress cannot go backwards or change after completion.")
    if quantity >= job.quantity_planned:
        raise HTTPException(422, "Use completion to post the full job quantity.")
    if job.started_at and quantity == job.quantity_completed:
        return job
    job.started_at = job.started_at or datetime.now(timezone.utc)
    job.quantity_completed = quantity
    if order.status == "ready_for_production":
        order.status = "in_production"
    record_audit_event(db, actor_user_id=actor_id, action="production_job.progress",
                       entity_type="production_job", entity_id=job.id, detail={"quantity": str(quantity)})
    db.commit()
    return job


def issue_components(db: Session, job: ProductionJob):
    part_ids = [line.component_product_id for line in job.components]
    balances = {row.product_id: row for row in db.scalars(select(Inventory).where(
        Inventory.product_id.in_([*part_ids, job.product_id])
    ).order_by(Inventory.product_id).with_for_update().execution_options(populate_existing=True))}
    reserved = defaultdict(list)
    for row in db.scalars(select(Reservation).where(
        Reservation.order_id == job.order_id, Reservation.product_id.in_(part_ids)
    ).order_by(Reservation.id).execution_options(populate_existing=True)):
        reserved[row.product_id].append(row)
    for line in job.components:
        remaining = line.quantity_required
        for row in reserved[line.component_product_id]:
            used = min(remaining, row.quantity)
            remaining -= used
            if used == row.quantity:
                db.delete(row)
            else:
                row.quantity -= used
            if remaining == 0:
                break
        if remaining:
            balance = balances.get(line.component_product_id)
            if balance is None or balance.available < remaining:
                raise HTTPException(409, f"Insufficient stock for component {line.component_product_id}.")
            balance.available -= remaining


def complete_job(db: Session, job_id: int, actor_id: int) -> ProductionJob:
    order, job = locked_job(db, job_id)
    if job.completed_at:
        return job
    check_revision(order, job.revision)
    issue_components(db, job)
    # Output goes directly into this order's reserved stock. Physical stock is
    # the sum of available stock and reservations in the inventory model.
    add_stock(db, job.product_id, Decimal(0))
    db.add(Reservation(order_id=order.id, product_id=job.product_id,
                       quantity=job.quantity_planned, user_id=actor_id))
    job.completed_at = datetime.now(timezone.utc)
    job.started_at = job.started_at or job.completed_at
    job.quantity_completed = job.quantity_planned
    db.flush()
    unfinished = db.scalar(select(ProductionJob.id).where(
        ProductionJob.order_id == order.id, ProductionJob.revision == job.revision,
        ProductionJob.completed_at.is_(None)).limit(1))
    if unfinished is None:
        order.status = "ready_for_delivery"
    elif order.status == "ready_for_production":
        order.status = "in_production"
    record_audit_event(db, actor_user_id=actor_id, action="production_job.completed",
                       entity_type="production_job", entity_id=job.id, detail={
                           "product_id": job.product_id, "quantity_produced": str(job.quantity_planned),
                           "components_consumed": {str(line.component_product_id): str(line.quantity_required)
                                                   for line in job.components},
                       })
    db.commit()
    return job
