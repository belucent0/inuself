"""add status column to content"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "21e26f8fc631"
down_revision = "20241124_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ContentStatus enum 생성
    content_status_enum = postgresql.ENUM(
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "RETRYING",
        name="contentstatus",
        create_type=True,
    )
    content_status_enum.create(op.get_bind(), checkfirst=True)
    
    # status 컬럼 추가 (기본값 QUEUED)
    op.add_column(
        "content",
        sa.Column(
            "status",
            content_status_enum,
            nullable=False,
            server_default="QUEUED",
        ),
    )
    
    # 인덱스 추가
    op.create_index("ix_content_status", "content", ["status"])


def downgrade() -> None:
    op.drop_index("ix_content_status", table_name="content")
    op.drop_column("content", "status")
    op.execute("DROP TYPE IF EXISTS contentstatus")
