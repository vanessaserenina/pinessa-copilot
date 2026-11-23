"""Track submission and independent approval of purchase orders."""
from alembic import op
import sqlalchemy as sa

revision = "85dc93fe10aa"
down_revision = "5a307bc19e42"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("submitted_by", sa.BigInteger(), sa.ForeignKey("users.id")))
    op.add_column("purchase_orders", sa.Column("approved_by", sa.BigInteger(), sa.ForeignKey("users.id")))
    op.drop_constraint("status_valid", "purchase_orders", type_="check")
    op.create_check_constraint(
        "status_valid", "purchase_orders",
        "(status = 'draft' AND submitted_by IS NULL AND approved_by IS NULL) OR "
        "(status = 'pending_approval' AND submitted_by IS NOT NULL AND approved_by IS NULL) OR "
        "(status = 'approved' AND submitted_by IS NOT NULL AND approved_by IS NOT NULL "
        "AND approved_by <> submitted_by AND approved_by <> created_by)",
    )


def downgrade():
    op.drop_constraint("status_valid", "purchase_orders", type_="check")
    # Restoring the draft-only schema requires all POs to still be drafts.
    op.create_check_constraint("status_valid", "purchase_orders", "status = 'draft'")
    op.drop_column("purchase_orders", "approved_by")
    op.drop_column("purchase_orders", "submitted_by")
