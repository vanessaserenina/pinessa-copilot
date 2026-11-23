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


    # Older extractions allowed any number of lines, including empty proposals.
    op.execute("""
        WITH moved AS (
            UPDATE ai_extractions
            SET status = 'needs_manual_handling', error_message = 'Confirmation requires 1 to 100 order lines.'
            WHERE status = 'needs_review' AND json_array_length(validated_output->'lines') NOT BETWEEN 1 AND 100
            RETURNING id, order_source_id
        )
        INSERT INTO audit_events (action, entity_type, entity_id, detail)
        SELECT 'ai_extraction.needs_manual_handling', 'ai_extraction', id,
               jsonb_build_object('source_id', order_source_id, 'reason', 'line_count')
        FROM moved
    """)


def downgrade():
    op.drop_column("ai_extractions", "confirmed_order_id")
    op.drop_column("ai_extractions", "line_matches")
