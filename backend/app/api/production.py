from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.orders import OrderDB
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.orders.service import read_order
from app.modules.production.models import ProductionJob
from app.modules.production.service import complete_job, jobs_for_revision, plan_jobs, record_progress

router = APIRouter(prefix="/production", tags=["production"])
ProductionManager = Annotated[User, Depends(require_permission("production:manage"))]
JOBS_PATH = "/orders/{order_id}/revisions/{revision}/jobs"


class ComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    component_product_id: int
    quantity_required: Decimal


class ProgressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity_completed: Decimal = Field(ge=0, max_digits=23, decimal_places=4)


class JobOut(BaseModel):
    id: int
    order_id: int
    revision: int
    product_id: int
    bom_id: int
    quantity_planned: Decimal
    components: list[ComponentOut]
    quantity_completed: Decimal
    started_at: datetime | None
    completed_at: datetime | None
    status: Literal["planned", "in_progress", "completed", "superseded"]


def job_output(job: ProductionJob, current_revision: int) -> JobOut:
    status = "planned"
    if job.completed_at:
        status = "completed"
    elif job.revision != current_revision:
        status = "superseded"
    elif job.started_at:
        status = "in_progress"
    return JobOut(id=job.id, order_id=job.order_id, revision=job.revision,
                  product_id=job.product_id, bom_id=job.bom_id, quantity_planned=job.quantity_planned,
                  components=[ComponentOut.model_validate(line) for line in job.components],
                  quantity_completed=job.quantity_completed, started_at=job.started_at,
                  completed_at=job.completed_at, status=status)


@router.put(JOBS_PATH)
def plan(order_id: int, revision: int, db: OrderDB, actor: ProductionManager) -> list[JobOut]:
    return [job_output(job, revision) for job in plan_jobs(db, order_id, revision, actor.id)]


@router.get(JOBS_PATH)
def list_jobs(order_id: int, revision: int, db: OrderDB, _: ProductionManager) -> list[JobOut]:
    order = read_order(db, order_id)
    return [job_output(job, order.revision) for job in jobs_for_revision(db, order_id, revision)]


@router.put("/jobs/{job_id}/progress")
def progress(job_id: int, data: ProgressInput, db: OrderDB, actor: ProductionManager) -> JobOut:
    job = record_progress(db, job_id, data.quantity_completed, actor.id)
    return job_output(job, job.revision)


@router.put("/jobs/{job_id}/complete")
def complete(job_id: int, db: OrderDB, actor: ProductionManager) -> JobOut:
    job = complete_job(db, job_id, actor.id)
    return job_output(job, job.revision)
