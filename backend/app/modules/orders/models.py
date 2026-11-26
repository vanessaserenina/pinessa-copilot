from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Identity, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("customer_code", "customer_po_number", name="uq_orders_customer_po"),
        CheckConstraint("status in ('draft','awaiting_materials','ready_for_production','ready_for_delivery',"
                        "'in_production','partially_dispatched','dispatched','delivered')",
                        name="status_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    customer_code: Mapped[str] = mapped_column(String(80), index=True)
    customer_po_number: Mapped[str | None] = mapped_column(String(120))
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(25), default="draft")
    revision: Mapped[int] = mapped_column(default=1)
    line_signature: Mapped[str] = mapped_column(String(64))
    stock: Mapped[dict | None] = mapped_column(JSONB)
    items: Mapped[list["OrderItem"]] = relationship(cascade="all, delete-orphan", order_by="OrderItem.id")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0 and unit_price >= 0", name="values_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))


class OrderRevision(TimestampMixin, Base):
    __tablename__ = "order_revisions"

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500))
    snapshot: Mapped[dict] = mapped_column(JSONB)

class OrderSource(TimestampMixin, Base):
    __tablename__ = "order_sources"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(12))
    raw_text: Mapped[str | None]
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="received")

class CustomerPriceTier(Base):
    __tablename__ = "customer_price_tiers"

    customer_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    tier: Mapped[str] = mapped_column(String(80))


class ApprovedSalesPrice(Base):
    __tablename__ = "approved_sales_prices"
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="nonnegative_price"),
        CheckConstraint("valid_through >= valid_from", name="valid_interval"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    tier: Mapped[str] = mapped_column(String(80), primary_key=True)
    selling_unit: Mapped[str] = mapped_column(String(20), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    book: Mapped[str] = mapped_column(String(120), primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_through: Mapped[date] = mapped_column(Date)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
