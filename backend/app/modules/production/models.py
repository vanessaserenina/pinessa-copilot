from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin


class ProductionJob(TimestampMixin, Base):
    __tablename__ = "production_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "revision"], ["order_revisions.order_id", "order_revisions.revision"]),
        UniqueConstraint("order_id", "revision", "product_id"),
        CheckConstraint("quantity_planned > 0", name="positive_quantity"),
        CheckConstraint("quantity_completed >= 0 AND quantity_completed <= quantity_planned", name="progress_range"),
        CheckConstraint("(completed_at IS NOT NULL) = (quantity_completed = quantity_planned)", name="completion_quantity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int]
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    quantity_planned: Mapped[Decimal] = mapped_column(Numeric(23, 4))
    quantity_completed: Mapped[Decimal] = mapped_column(Numeric(23, 4), server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    components: Mapped[list["JobComponent"]] = relationship(order_by="JobComponent.component_product_id")


class JobComponent(Base):
    __tablename__ = "production_job_components"
    __table_args__ = (CheckConstraint("quantity_required > 0", name="positive_quantity"),)

    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id"), primary_key=True)
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity_required: Mapped[Decimal] = mapped_column(Numeric(23, 4))
