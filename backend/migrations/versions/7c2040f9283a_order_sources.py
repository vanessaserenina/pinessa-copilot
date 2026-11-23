"""Preserve original order sources and reject repeated content."""
import sqlalchemy as sa
from alembic import op

revision = "7c2040f9283a"
down_revision = "d4238eaf7c16"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source_type", sa.String(12), nullable=False),
        sa.Column("raw_text", sa.String()),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("storage_key", sa.String(32)),
        sa.Column("content_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("submitted_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("order_sources")
