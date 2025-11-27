"""create content and stt_log tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20251124_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("speakers", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("transcription", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_content_id", "content", ["id"])

    op.create_table(
        "stt_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("content_id", sa.Integer, sa.ForeignKey("content.id", ondelete="CASCADE")),
        sa.Column("log", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stt_log")
    op.drop_index("ix_content_id", table_name="content")
    op.drop_table("content")


