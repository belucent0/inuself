"""Add status column to ai_message table.

Revision ID: 20260211_01
Revises: 20260210_01
Create Date: 2026-02-11

Phase C-1: 메시지 상태 관리
- status 컬럼 추가 (pending | generating | completed | failed | cancelled)
- 기존 메시지는 모두 'completed'로 설정
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260211_01"
down_revision: Union[str, None] = "20260210_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ai_message 테이블에 status 컬럼 추가
    op.add_column(
        "ai_message",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="completed",  # 기존 메시지는 모두 completed
        ),
    )

    # status 컬럼에 인덱스 추가 (generating 상태 메시지 조회용)
    op.create_index(
        "ix_ai_message_status",
        "ai_message",
        ["status"],
    )

    # 복합 인덱스: thread_id + status (스레드별 generating 메시지 조회)
    op.create_index(
        "ix_ai_message_thread_status",
        "ai_message",
        ["thread_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_message_thread_status", table_name="ai_message")
    op.drop_index("ix_ai_message_status", table_name="ai_message")
    op.drop_column("ai_message", "status")
