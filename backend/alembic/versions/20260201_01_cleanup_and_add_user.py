"""cleanup orphan data and add user table

Revision ID: 20260201_01
Revises: 20260131_07
Create Date: 2026-02-01

1. 연결이 끊긴 Transcription, Document 삭제
2. 모든 테스트 데이터 삭제 (선택적)
3. User 테이블 생성
4. Content 테이블에 is_public, is_deleted 컬럼 추가
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260201_01'
down_revision: Union[str, None] = '20260131_07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 연결 끊긴 Transcription 삭제
    op.execute("DELETE FROM transcription WHERE content_id IS NULL")

    # 2. 연결 끊긴 Document 삭제
    op.execute("DELETE FROM document WHERE content_id IS NULL")

    # 3. 모든 데이터 삭제 (테스트 데이터 정리)
    op.execute("DELETE FROM stt_log")
    op.execute("DELETE FROM llm_log")
    op.execute("DELETE FROM transcription")
    op.execute("DELETE FROM document")
    op.execute("DELETE FROM content")
    op.execute("DELETE FROM file")

    # 4. User 테이블 생성
    op.create_table(
        'user',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('password', sa.String(255), nullable=False),
        sa.Column('name', sa.String(100), nullable=True),
        sa.Column('avatar_url', sa.String(512), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_super', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # 5. 인덱스 생성
    op.create_index('ix_user_email', 'user', ['email'], unique=True)
    op.create_index('ix_user_created_at', 'user', ['created_at'])

    # 6. Content 테이블에 is_public, is_deleted 컬럼 추가
    op.add_column('content', sa.Column('is_public', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('content', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    # Content 테이블 컬럼 삭제
    op.drop_column('content', 'is_deleted')
    op.drop_column('content', 'is_public')

    # User 테이블 삭제
    op.drop_index('ix_user_created_at', 'user')
    op.drop_index('ix_user_email', 'user')
    op.drop_table('user')

    # 데이터 복구는 불가능 (백업 필요)
