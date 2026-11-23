"""Draft purchase requisitions from confirmed order revisions."""
from alembic import op
import sqlalchemy as sa

revision = "671bfac3c102"
down_revision = "9c5017af321a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_requisitions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source_order_id", sa.BigInteger(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(25), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["source_order_id", "source_revision"],
                                ["order_revisions.order_id", "order_revisions.revision"]),
        sa.UniqueConstraint("source_order_id", "source_revision"),
        sa.CheckConstraint("status = 'draft'", name="status_valid"),
    )
    op.create_table(
        "purchase_requisition_items",
        sa.Column("requisition_id", sa.BigInteger(), sa.ForeignKey("purchase_requisitions.id"), primary_key=True),
        sa.Column("component_product_id", sa.BigInteger(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("quantity_needed", sa.Numeric(23, 4), nullable=False),
        sa.CheckConstraint("quantity_needed > 0", name="positive_quantity"),
    )


def downgrade():
    op.drop_table("purchase_requisition_items")
    op.drop_table("purchase_requisitions")
