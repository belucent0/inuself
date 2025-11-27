"""add object_key to content"""

from alembic import op
import sqlalchemy as sa


revision = "20251124_02"
down_revision = "20251124_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content",
        sa.Column("object_key", sa.String(length=1024), nullable=False, server_default=""),
    )
    op.execute("UPDATE content SET object_key = filename WHERE object_key = ''")
    op.alter_column("content", "object_key", server_default=None)


def downgrade() -> None:
    op.drop_column("content", "object_key")


