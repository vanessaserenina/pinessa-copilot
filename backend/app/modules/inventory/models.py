from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base

# A BOM total can have 16 whole digits at 10 million units and 100% scrap.
QUANTITY_PRECISION = 20
QUANTITY_SCALE = 4


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (CheckConstraint("available >= 0", name="available_nonnegative"),)

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    available: Mapped[Decimal] = mapped_column(Numeric(23, 4), nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("inventory.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(23, QUANTITY_SCALE))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
