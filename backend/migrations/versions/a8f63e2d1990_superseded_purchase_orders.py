"""Retain pre-dispatch POs replaced after a source order revision."""
from alembic import op

revision = "a8f63e2d1990"
down_revision = "18e21f9c47ab"
branch_labels = None
depends_on = None


def replace_checks(*, allow_superseded):
    pre_dispatch = "'draft', 'pending_approval', 'approved'"
    superseded = ""
    if allow_superseded:
        pre_dispatch += ", 'superseded'"
        superseded = ("(status = 'superseded' AND (approved_by IS NULL OR (submitted_by IS NOT NULL "
                      "AND approved_by <> submitted_by AND approved_by <> created_by))) OR ")
    for name in ("status_valid", "sent_details_valid"):
        op.drop_constraint(name, "purchase_orders", type_="check")
    op.create_check_constraint(
        "status_valid", "purchase_orders",
        "(status = 'draft' AND submitted_by IS NULL AND approved_by IS NULL) OR "
        "(status = 'pending_approval' AND submitted_by IS NOT NULL AND approved_by IS NULL) OR "
        f"{superseded}(status IN ('approved', 'sent', 'partially_received', 'received') "
        "AND submitted_by IS NOT NULL AND approved_by IS NOT NULL "
        "AND approved_by <> submitted_by AND approved_by <> created_by)",
    )
    op.create_check_constraint(
        "sent_details_valid", "purchase_orders",
        "(status IN ('sent', 'partially_received', 'received') AND sent_by IS NOT NULL AND sent_at IS NOT NULL) OR "
        f"(status IN ({pre_dispatch}) AND sent_by IS NULL AND sent_at IS NULL)",
    )


def upgrade():
    replace_checks(allow_superseded=True)


def downgrade():
    # Preserve replacement history if the previous schema cannot represent these rows.
    replace_checks(allow_superseded=False)
