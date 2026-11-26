"""Approved sales price books and customer tier assignments."""
from alembic import op
import sqlalchemy as sa

revision = "fd8a93b571c2"
down_revision = "ab38d71cef40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("customer_price_tiers",
                    sa.Column("customer_code", sa.String(80), primary_key=True),
                    sa.Column("tier", sa.String(80), nullable=False))
    op.create_table("approved_sales_prices",
                    sa.Column("product_id", sa.BigInteger, sa.ForeignKey("products.id"), primary_key=True),
                    sa.Column("tier", sa.String(80), primary_key=True),
                    sa.Column("selling_unit", sa.String(20), primary_key=True),
                    sa.Column("currency", sa.String(3), primary_key=True),
                    sa.Column("book", sa.String(120), primary_key=True),
                    sa.Column("valid_from", sa.Date, nullable=False),
                    sa.Column("valid_through", sa.Date, nullable=False),
                    sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
                    sa.CheckConstraint("unit_price >= 0", name="nonnegative_price"),
                    sa.CheckConstraint("valid_through >= valid_from", name="valid_interval"))


def downgrade():
    op.drop_table("approved_sales_prices")
    op.drop_table("customer_price_tiers")
