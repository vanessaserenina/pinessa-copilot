"""Store reviewable LLM proposals separately from customer orders."""
from alembic import op
import sqlalchemy as sa

revision = "d89bc69ed521"
down_revision = "7c2040f9283a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_extractions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_source_id", sa.BigInteger(), sa.ForeignKey("order_sources.id"), unique=True, nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("raw_output", sa.JSON(), nullable=False),
        sa.Column("validated_output", sa.JSON()),
        sa.Column("schema_valid", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("ai_extractions")
