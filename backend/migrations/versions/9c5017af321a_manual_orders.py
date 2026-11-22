"""Manual orders, revisions, and order-linked inventory reservations."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "9c5017af321a"
down_revision = "c744f1902c51"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")]


def upgrade():
    op.create_table("orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("customer_code", sa.String(80), nullable=False),
        sa.Column("customer_po_number", sa.String(120)),
        sa.Column("requested_delivery_date", sa.Date()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(25), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("line_signature", sa.String(64), nullable=False),
        sa.Column("stock", JSONB()),
        *timestamps(),
        sa.UniqueConstraint("customer_code", "customer_po_number", name="uq_orders_customer_po"),
        sa.CheckConstraint("status in ('draft','awaiting_materials','ready_for_production','ready_for_delivery')",
                           name="status_valid"))
    op.create_index("ix_orders_customer_code", "orders", ["customer_code"])
    op.create_table("order_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.CheckConstraint("quantity > 0 and unit_price >= 0", name="values_valid"))
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_table("order_revisions",
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False), *timestamps())
    op.add_column("reservations", sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id")))
    op.create_index("ix_reservations_order_id", "reservations", ["order_id"])
    # An order may aggregate component needs from up to 100 lines.
    op.alter_column("reservations", "quantity", type_=sa.Numeric(23, 4))


def downgrade():
    op.alter_column("reservations", "quantity", type_=sa.Numeric(20, 4))
    op.drop_column("reservations", "order_id")
    op.drop_table("order_revisions")
    op.drop_table("order_items")
    op.drop_table("orders")
