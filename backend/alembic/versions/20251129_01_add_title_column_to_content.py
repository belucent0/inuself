"""add title column to content table

Revision ID: 20251129_01
Revises: 20251128_01_rename_failed_to_asr_failed
Create Date: 2025-11-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251129_01'
down_revision: Union[str, None] = '20251128_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """title 컬럼 추가 (nullable=True, LLM 요약 후 생성됨)"""
    op.add_column(
        'content',
        sa.Column('title', sa.String(length=512), nullable=True)
    )


def downgrade() -> None:
    """title 컬럼 제거"""
    op.drop_column('content', 'title')

