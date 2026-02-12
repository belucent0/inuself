"""Add partial_content column to ai_message table for v1.0.0.

Revision ID: 20260212_01
Revises: 20260211_01
Create Date: 2026-02-12

v1.0.0: 채팅 아키텍처 개선
- partial_content 컬럼 추가 (스트리밍 중 2초마다 저장되는 부분 답변)
- status enum 확장 (queued|analyzing|searching|thinking|generating|completed|failed)
  → String(20)이므로 enum 확장은 DB 변경 불필요, 애플리케이션 레벨 처리
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260212_01"
down_revision: Union[str, None] = "20260211_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # partial_content 컬럼 추가 (스트리밍 중 부분 응답 저장)
    op.add_column(
        "ai_message",
        sa.Column(
            "partial_content",
            sa.Text(),
            nullable=True,
            comment="스트리밍 중 2초마다 저장되는 부분 답변",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_message", "partial_content")
