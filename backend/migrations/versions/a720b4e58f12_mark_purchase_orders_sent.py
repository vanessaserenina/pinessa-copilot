"""Record manual dispatch of approved purchase orders."""
from alembic import op
import sqlalchemy as sa

revision = "a720b4e58f12"
down_revision = "85dc93fe10aa"
branch_labels = None
depends_on = None


def replace_status_check(approved_states):
    op.drop_constraint("status_valid", "purchase_orders", type_="check")
    op.create_check_constraint(
        "status_valid", "purchase_orders",
        "(status = 'draft' AND submitted_by IS NULL AND approved_by IS NULL) OR "
        "(status = 'pending_approval' AND submitted_by IS NOT NULL AND approved_by IS NULL) OR "
        f"({approved_states} AND submitted_by IS NOT NULL AND approved_by IS NOT NULL "
        "AND approved_by <> submitted_by AND approved_by <> created_by)",
    )


def upgrade():
    op.add_column("purchase_orders", sa.Column("sent_by", sa.BigInteger(), sa.ForeignKey("users.id")))
    op.add_column("purchase_orders", sa.Column("sent_at", sa.DateTime(timezone=True)))
    replace_status_check("status IN ('approved', 'sent')")
    op.create_check_constraint(
        "sent_details_valid", "purchase_orders",
        "(status = 'sent' AND sent_by IS NOT NULL AND sent_at IS NOT NULL) OR "
        "(status <> 'sent' AND sent_by IS NULL AND sent_at IS NULL)",
    )


def downgrade():
    # The previous schema cannot represent sent POs. Preserve their history if any exist.
    replace_status_check("status = 'approved'")
    op.drop_constraint("sent_details_valid", "purchase_orders", type_="check")
    op.drop_column("purchase_orders", "sent_at")
    op.drop_column("purchase_orders", "sent_by")
