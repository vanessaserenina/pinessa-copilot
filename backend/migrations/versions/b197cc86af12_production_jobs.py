"""Plan production jobs against confirmed order revisions."""
from alembic import op
import sqlalchemy as sa

revision = "b197cc86af12"
down_revision = "a8f63e2d1990"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "production_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("bom_id", sa.BigInteger(), sa.ForeignKey("boms.id"), nullable=False),
        sa.Column("quantity_planned", sa.Numeric(23, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["order_id", "revision"], ["order_revisions.order_id", "order_revisions.revision"]),
        sa.UniqueConstraint("order_id", "revision", "product_id"),
        sa.CheckConstraint("quantity_planned > 0", name="positive_quantity"),
    )
    op.create_table(
        "production_job_components",
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("production_jobs.id"), primary_key=True),
        sa.Column("component_product_id", sa.BigInteger(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("quantity_required", sa.Numeric(23, 4), nullable=False),
        sa.CheckConstraint("quantity_required > 0", name="positive_quantity"),
    )


def downgrade():
    op.drop_table("production_job_components")
    op.drop_table("production_jobs")
