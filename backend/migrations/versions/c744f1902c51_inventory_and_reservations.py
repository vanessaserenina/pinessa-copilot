"""Add per-product inventory and reservations."""
import sqlalchemy as sa
from alembic import op

revision = "c744f1902c51"
down_revision = "abeea08a2b10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory",
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("available", sa.Numeric(23, 4), nullable=False),
        sa.CheckConstraint("available >= 0", name="available_nonnegative"),
    )
    op.create_table(
        "reservations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("inventory.product_id"), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    )


def downgrade():
    op.drop_table("reservations")
    op.drop_table("inventory")
