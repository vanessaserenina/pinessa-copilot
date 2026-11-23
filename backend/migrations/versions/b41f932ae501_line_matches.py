"""Store deterministic line matches separately from the original proposal."""
from alembic import op
import sqlalchemy as sa

revision = "b41f932ae501"
down_revision = "d89bc69ed521"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_extractions", sa.Column("line_matches", sa.JSON()))
    op.add_column("ai_extractions", sa.Column(
        "confirmed_order_id", sa.BigInteger(), sa.ForeignKey("orders.id"), unique=True))


def downgrade():
    op.drop_column("ai_extractions", "confirmed_order_id")
    op.drop_column("ai_extractions", "line_matches")
