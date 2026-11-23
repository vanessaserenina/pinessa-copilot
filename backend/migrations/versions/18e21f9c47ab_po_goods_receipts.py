"""Record PO receipts and cumulative received quantities."""
from alembic import op
import sqlalchemy as sa

revision = "18e21f9c47ab"
down_revision = "a720b4e58f12"
branch_labels = None
depends_on = None


def replace_status_checks(dispatched_states):
    for name in ("status_valid", "sent_details_valid"):
        op.drop_constraint(name, "purchase_orders", type_="check")
    op.create_check_constraint(
        "status_valid", "purchase_orders",
        "(status = 'draft' AND submitted_by IS NULL AND approved_by IS NULL) OR "
        "(status = 'pending_approval' AND submitted_by IS NOT NULL AND approved_by IS NULL) OR "
        f"(status IN ('approved', {dispatched_states}) AND submitted_by IS NOT NULL AND approved_by IS NOT NULL "
        "AND approved_by <> submitted_by AND approved_by <> created_by)",
    )
    op.create_check_constraint(
        "sent_details_valid", "purchase_orders",
        f"(status IN ({dispatched_states}) AND sent_by IS NOT NULL AND sent_at IS NOT NULL) OR "
        "(status IN ('draft', 'pending_approval', 'approved') AND sent_by IS NULL AND sent_at IS NULL)",
    )


def upgrade():
    replace_status_checks("'sent', 'partially_received', 'received'")
    op.add_column("purchase_order_items", sa.Column("quantity_received", sa.Numeric(23, 4),
                                                     server_default="0", nullable=False))
    op.create_check_constraint("received_valid", "purchase_order_items", "quantity_received BETWEEN 0 AND quantity")
    op.create_table(
        "goods_receipt_lines",
        sa.Column("purchase_order_id", sa.BigInteger(), primary_key=True),
        sa.Column("receipt_id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), primary_key=True),
        sa.Column("quantity", sa.Numeric(23, 4), nullable=False),
        sa.Column("received_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_order_id", "product_id"],
                                ["purchase_order_items.purchase_order_id", "purchase_order_items.product_id"]),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    )


def downgrade():
    replace_status_checks("'sent'")
    op.drop_table("goods_receipt_lines")
    op.drop_constraint("received_valid", "purchase_order_items", type_="check")
    op.drop_column("purchase_order_items", "quantity_received")
