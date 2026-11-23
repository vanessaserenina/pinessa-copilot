"""Track per-user read receipts for operational notifications."""
from alembic import op
import sqlalchemy as sa

revision = "ab38d71cef40"
down_revision = "b41f932ae501"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notification_reads",
                    sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), primary_key=True),
                    sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("audit_events.id"), primary_key=True),
                    sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table("notification_reads")
