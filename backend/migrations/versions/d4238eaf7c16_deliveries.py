"""Delivery records, line quantities, and document attachments."""
from alembic import op
import sqlalchemy as sa

revision = "d4238eaf7c16"
down_revision = "8acf43d27f01"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")]


def order_statuses(values):
    op.drop_constraint(op.f("ck_orders_status_valid"), "orders", type_="check")
    op.create_check_constraint("status_valid", "orders", f"status in ({values})")


original = "'draft','awaiting_materials','ready_for_production','ready_for_delivery'"


def upgrade():
    order_statuses(original + ",'in_production','partially_dispatched','dispatched','delivered'")
    op.create_table("deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("delivery_number", sa.String(80), unique=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("planned_date", sa.Date()),
        sa.Column("address_snapshot", sa.String(500), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)), *timestamps(),
        sa.ForeignKeyConstraint(["order_id", "revision"], ["order_revisions.order_id", "order_revisions.revision"]),
        sa.CheckConstraint("status in ('pending','ready','dispatched','delivered','superseded')", name="status_valid"))
    op.create_table("delivery_items",
        sa.Column("delivery_id", sa.BigInteger(), sa.ForeignKey("deliveries.id"), primary_key=True),
        sa.Column("line_no", sa.Integer(), primary_key=True),
        sa.Column("order_item_id", sa.BigInteger(), sa.ForeignKey("order_items.id", ondelete="SET NULL")),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="positive_quantity"))
    op.create_table("delivery_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("delivery_id", sa.BigInteger(), sa.ForeignKey("deliveries.id"), index=True, nullable=False),
        sa.Column("uploaded_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048)), sa.Column("storage_key", sa.String(32)),
        sa.Column("sha256", sa.String(64)), sa.Column("mime_type", sa.String(255)), *timestamps(),
        sa.CheckConstraint("document_type in ('delivery_note','invoice')", name="document_type_valid"),
        sa.CheckConstraint("(storage_key IS NULL) = (sha256 IS NULL) AND "
                           "(storage_key IS NULL) <> (url IS NULL)", name="document_source"))


def downgrade():
    op.execute("UPDATE orders SET status = 'ready_for_production' WHERE status = 'in_production'")
    # Shipped orders cannot safely be represented by the earlier status machine.
    order_statuses(original)
    op.drop_table("delivery_documents")
    op.drop_table("delivery_items")
    op.drop_table("deliveries")
