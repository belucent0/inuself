"""remove content legacy fields

Revision ID: 20260131_03
Revises: 20260131_02
Create Date: 2026-01-31

Phase 2: Content 테이블의 레거시 필드 제거
- speakers: Transcription.speakers로 이전됨
- duration_seconds: Transcription.duration_seconds로 이전됨
- transcription: Transcription.transcription으로 이전됨
- filename: File.filename으로 이전됨
- object_key: File.object_key로 이전됨
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260131_03'
down_revision: Union[str, None] = '20260131_02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 레거시 필드 삭제
    op.drop_column('content', 'speakers')
    op.drop_column('content', 'duration_seconds')
    op.drop_column('content', 'transcription')
    op.drop_column('content', 'filename')
    op.drop_column('content', 'object_key')


def downgrade() -> None:
    # 레거시 필드 복원
    op.add_column(
        'content',
        sa.Column('object_key', sa.String(1024), nullable=True)
    )
    op.add_column(
        'content',
        sa.Column('filename', sa.String(512), nullable=True)
    )
    op.add_column(
        'content',
        sa.Column('transcription', postgresql.JSONB, nullable=True)
    )
    op.add_column(
        'content',
        sa.Column('duration_seconds', sa.Float, server_default='0.0', nullable=False)
    )
    op.add_column(
        'content',
        sa.Column('speakers', postgresql.ARRAY(sa.String), server_default='{}', nullable=False)
    )
