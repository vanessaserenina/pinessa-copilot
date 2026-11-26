"""Customer names for approved price tier lookup."""
from alembic import op
import sqlalchemy as sa

revision = "ac11343e21ad"
down_revision = "fd8a93b571c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customer_price_tiers", sa.Column("customer_name", sa.String(250)))


def downgrade():
    op.drop_column("customer_price_tiers", "customer_name")
