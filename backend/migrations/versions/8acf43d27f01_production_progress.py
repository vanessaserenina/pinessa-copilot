"""Track progress and completion on existing production jobs."""
from alembic import op
import sqlalchemy as sa

revision = "8acf43d27f01"
down_revision = "b197cc86af12"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("production_jobs", sa.Column("quantity_completed", sa.Numeric(23, 4),
                                              nullable=False, server_default="0"))
    op.add_column("production_jobs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("production_jobs", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("progress_range", "production_jobs",
                               "quantity_completed >= 0 AND quantity_completed <= quantity_planned")
    op.create_check_constraint("completion_quantity", "production_jobs",
                               "(completed_at IS NOT NULL) = (quantity_completed = quantity_planned)")


def downgrade():
    op.drop_constraint("completion_quantity", "production_jobs", type_="check")
    op.drop_constraint("progress_range", "production_jobs", type_="check")
    op.drop_column("production_jobs", "completed_at")
    op.drop_column("production_jobs", "started_at")
    op.drop_column("production_jobs", "quantity_completed")
