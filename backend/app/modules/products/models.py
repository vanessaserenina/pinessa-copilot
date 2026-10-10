from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin

class ProductCategory(TimestampMixin, Base):
    """Section (top level) or group (child), e.g. PERLENGKAPAN MANDI > Sabun."""

    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("product_categories.id", ondelete="RESTRICT"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    parent: Mapped["ProductCategory | None"] = relationship(
        remote_side="ProductCategory.id", back_populates="children"
    )
    children: Mapped[list["ProductCategory"]] = relationship(back_populates="parent")
    families: Mapped[list["ProductFamily"]] = relationship(back_populates="category")


class ProductFamily(TimestampMixin, Base):
    """A product line as listed in the catalogue, e.g. 'Sabun batang bulat'."""

    __tablename__ = "product_families"
    __table_args__ = (
        CheckConstraint("source IN ('catalogue', 'synthetic')", name="source_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_categories.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    # Allowed options, e.g. {"packaging": ["packaging", "non_packaging"], "branding": ["logo", "non_logo"]}
    option_groups: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    is_custom_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="catalogue", server_default="catalogue"
    )
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    category: Mapped[ProductCategory] = relationship(back_populates="families")
    products: Mapped[list["Product"]] = relationship(back_populates="family")


class Product(TimestampMixin, Base):
    """One orderable/stockable item (SKU) = a family + one option combination."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("family_id", "variant_key", name="uq_products_family_variant"),
        CheckConstraint("product_type IN ('FINISHED', 'COMPONENT')", name="product_type_valid"),
        CheckConstraint("source IN ('catalogue', 'synthetic')", name="source_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    family_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_families.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    # Canonical text of the chosen options (sorted keys), or 'default' when the family has none.
    variant_key: Mapped[str] = mapped_column(String(200), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    product_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FINISHED", server_default="FINISHED"
    )
    unit_of_measure: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pcs", server_default="pcs"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="catalogue", server_default="catalogue"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    family: Mapped[ProductFamily] = relationship(back_populates="products")


class ProductAlias(TimestampMixin, Base):
    """Alternative names, abbreviations, or spellings used by customers."""

    __tablename__ = "product_aliases"
    __table_args__ = (
        CheckConstraint(
            "(family_id IS NOT NULL) <> (product_id IS NOT NULL)", name="exactly_one_target"
        ),
        CheckConstraint(
            "status IN ('suggested', 'approved', 'rejected')", name="status_valid"
        ),
        Index(
            "uq_product_aliases_family_alias",
            "alias_normalized",
            "family_id",
            unique=True,
            postgresql_where=text("family_id IS NOT NULL"),
        ),
        Index(
            "uq_product_aliases_product_alias",
            "alias_normalized",
            "product_id",
            unique=True,
            postgresql_where=text("product_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    family_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("product_families.id", ondelete="CASCADE"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    alias_text: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="id", server_default="id"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="suggested", server_default="suggested"
    )
    # Foreign key to users is added in Stage 2, when the users table exists.
    approved_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
