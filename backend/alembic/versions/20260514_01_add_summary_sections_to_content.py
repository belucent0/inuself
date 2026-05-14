"""add summary_sections JSONB column to content

generic block schema 저장용 컬럼. LangGraph 섹션 점진 저장 + 부분 재시도 우산.
구조: { template_id, blocks[{ key, label, type, status, content, attempts, ... }], round, started_at, updated_at }

Revision ID: 20260514_01
Revises: 20260504_01
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260514_01"
down_revision: Union[str, None] = "20260504_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content",
        sa.Column(
            "summary_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("content", "summary_sections")
