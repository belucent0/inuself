"""add public_id to content table

Revision ID: 20260131_02
Revises: 20260131_01
Create Date: 2026-01-31

Phase 1: Content 테이블에 public_id (UUID v7) 컬럼 추가
- 외부 노출용 ID로 사용
- Phase 2에서 클라이언트 전환 후, Phase 3에서 PK로 승격 예정
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260131_02'
down_revision: Union[str, None] = '20260131_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. public_id 컬럼 추가 (nullable로 시작)
    op.add_column(
        'content',
        sa.Column('public_id', postgresql.UUID(as_uuid=True), nullable=True)
    )

    # 2. 기존 레코드에 UUID v7 생성하여 채우기
    # PostgreSQL의 gen_random_uuid()는 UUID v4이므로,
    # Python에서 UUID v7을 생성하여 업데이트하는 방식 사용
    # 여기서는 임시로 UUID v4 사용 (마이그레이션 시점에는 v4로 채움)
    op.execute("""
        UPDATE content
        SET public_id = gen_random_uuid()
        WHERE public_id IS NULL
    """)

    # 3. NOT NULL 제약 조건 추가
    op.alter_column('content', 'public_id', nullable=False)

    # 4. UNIQUE 인덱스 생성
    op.create_index('ix_content_public_id', 'content', ['public_id'], unique=True)


def downgrade() -> None:
    # 인덱스 제거
    op.drop_index('ix_content_public_id', 'content')

    # 컬럼 제거
    op.drop_column('content', 'public_id')
