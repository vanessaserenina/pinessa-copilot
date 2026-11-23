from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Identity, Numeric, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin


class PurchaseRequisition(TimestampMixin, Base):
    __tablename__ = "purchase_requisitions"
    __table_args__ = (
        ForeignKeyConstraint(["source_order_id", "source_revision"],
                             ["order_revisions.order_id", "order_revisions.revision"]),
        UniqueConstraint("source_order_id", "source_revision"),
        CheckConstraint("status = 'draft'", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_order_id: Mapped[int] = mapped_column(BigInteger)
    source_revision: Mapped[int]
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(25), default="draft")
    items: Mapped[list["PurchaseRequisitionItem"]] = relationship(
        cascade="all, delete-orphan", order_by="PurchaseRequisitionItem.component_product_id"
    )

    @property
    def number(self) -> str:
        return f"PR-{self.id:06d}"


class PurchaseRequisitionItem(Base):
    __tablename__ = "purchase_requisition_items"
    __table_args__ = (CheckConstraint("quantity_needed > 0", name="positive_quantity"),)

    requisition_id: Mapped[int] = mapped_column(ForeignKey("purchase_requisitions.id"), primary_key=True)
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity_needed: Mapped[Decimal] = mapped_column(Numeric(23, 4))


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (CheckConstraint("unit_cost >= 0", name="cost_valid"),)

    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), primary_key=True)
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 0))


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (CheckConstraint("status = 'draft'", name="status_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey("purchase_requisitions.id"), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(25), default="draft")
    total: Mapped[Decimal] = mapped_column(Numeric())
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        cascade="all, delete-orphan", order_by="PurchaseOrderItem.product_id"
    )

    @property
    def po_number(self) -> str:
        return f"PO-{self.id:06d}"


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (CheckConstraint("quantity > 0 and unit_cost >= 0", name="values_valid"),)

    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(23, 4))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 0))
