"""add summary_queued status

Revision ID: 20251209_01
Revises: 20251208_01
Create Date: 2025-12-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20251209_01'
down_revision: Union[str, None] = '20251208_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # FileStatus enum에 SUMMARY_QUEUED 추가
    # PostgreSQL에서 enum에 값을 추가하는 방법
    op.execute("ALTER TYPE filestatus ADD VALUE IF NOT EXISTS 'SUMMARY_QUEUED'")


def downgrade() -> None:
    # PostgreSQL enum에서 값을 제거하는 것은 복잡하므로
    # downgrade는 지원하지 않음 (새 enum을 만들고 데이터를 옮겨야 함)
    # 필요시 수동으로 처리
    pass
