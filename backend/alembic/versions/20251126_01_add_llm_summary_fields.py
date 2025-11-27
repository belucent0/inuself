"""add summary_md column and llm_log table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20251126_01"
down_revision = "21e26f8fc631"
branch_labels = None
depends_on = None


OLD_STATUSES = (
    "QUEUED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "RETRYING",
)

NEW_STATUSES = OLD_STATUSES + (
    "SUMMARIZING",
    "SUMMARY_FAILED",
)


def upgrade() -> None:
    # enum 확장: 새 타입 생성 후 교체
    new_enum = postgresql.ENUM(*NEW_STATUSES, name="contentstatus_new")
    new_enum.create(op.get_bind(), checkfirst=True)

    # 기존 status 기본값 제거 후 타입 교체
    op.alter_column("content", "status", server_default=None)
    op.execute(
        "ALTER TABLE content "
        "ALTER COLUMN status TYPE contentstatus_new "
        "USING status::text::contentstatus_new"
    )
    op.execute("DROP TYPE contentstatus")
    op.execute("ALTER TYPE contentstatus_new RENAME TO contentstatus")
    op.execute("ALTER TABLE content ALTER COLUMN status SET DEFAULT 'QUEUED'")

    # summary_md 컬럼 추가
    op.add_column("content", sa.Column("summary_md", sa.Text(), nullable=True))

    # llm_log 테이블 생성
    op.create_table(
        "llm_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("content_id", sa.Integer, sa.ForeignKey("content.id", ondelete="CASCADE")),
        sa.Column("log", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_log")
    op.drop_column("content", "summary_md")

    # enum 복원: 새 타입 생성 후 교체
    old_enum = postgresql.ENUM(*OLD_STATUSES, name="contentstatus_old")
    old_enum.create(op.get_bind(), checkfirst=True)

    op.execute(
        "UPDATE content SET status = 'FAILED' "
        "WHERE status IN ('SUMMARIZING', 'SUMMARY_FAILED')"
    )
    op.execute(
        "ALTER TABLE content "
        "ALTER COLUMN status TYPE contentstatus_old "
        "USING status::text::contentstatus_old"
    )
    op.execute("DROP TYPE contentstatus")
    op.execute("ALTER TYPE contentstatus_old RENAME TO contentstatus")
    op.execute("ALTER TABLE content ALTER COLUMN status SET DEFAULT 'QUEUED'")

