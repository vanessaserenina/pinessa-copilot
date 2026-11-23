"""Supplier prices and draft purchase orders."""
from alembic import op
import sqlalchemy as sa

revision = "5a307bc19e42"
down_revision = "671bfac3c102"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")]


def upgrade():
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(80), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "supplier_products",
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), primary_key=True),
        sa.Column("component_product_id", sa.BigInteger(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("unit_cost", sa.Numeric(14, 0), nullable=False),
        sa.CheckConstraint("unit_cost >= 0", name="cost_valid"),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("requisition_id", sa.BigInteger(), sa.ForeignKey("purchase_requisitions.id"), unique=True, nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(25), nullable=False),
        sa.Column("total", sa.Numeric(), nullable=False),
        sa.CheckConstraint("status = 'draft'", name="status_valid"),
        *timestamps(),
    )
    op.create_table(
        "purchase_order_items",
        sa.Column("purchase_order_id", sa.BigInteger(), sa.ForeignKey("purchase_orders.id"), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("quantity", sa.Numeric(23, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 0), nullable=False),
        sa.CheckConstraint("quantity > 0 and unit_cost >= 0", name="values_valid"),
    )


def downgrade():
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("supplier_products")
    op.drop_table("suppliers")
