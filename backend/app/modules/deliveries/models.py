from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Identity, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin


class Delivery(TimestampMixin, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "revision"], ["order_revisions.order_id", "order_revisions.revision"]),
        CheckConstraint("status in ('pending','ready','dispatched','delivered','superseded')", name="status_valid"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    delivery_number: Mapped[str] = mapped_column(String(80), unique=True)
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    revision: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    planned_date: Mapped[date | None]
    address_snapshot: Mapped[str] = mapped_column(String(500))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list["DeliveryItem"]] = relationship(order_by="DeliveryItem.line_no")
    documents: Mapped[list["DeliveryDocument"]] = relationship(order_by="DeliveryDocument.id")


class DeliveryItem(Base):
    __tablename__ = "delivery_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_quantity"),)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), primary_key=True)
    line_no: Mapped[int] = mapped_column(primary_key=True)
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id", ondelete="SET NULL"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]


class DeliveryDocument(TimestampMixin, Base):
    __tablename__ = "delivery_documents"
    __table_args__ = (
        CheckConstraint("document_type in ('delivery_note','invoice')", name="document_type_valid"),
        CheckConstraint("(storage_key IS NULL) = (sha256 IS NULL) AND "
                        "(storage_key IS NULL) <> (url IS NULL)", name="document_source"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    document_type: Mapped[str] = mapped_column(String(20))
    file_name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2048))
    storage_key: Mapped[str | None] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str | None] = mapped_column(String(255))
