"""add user_id to content table for multi-tenant data isolation

Revision ID: 20260216_02
Revises: 20260216_01
Create Date: 2026-02-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text


revision: str = "20260216_02"
down_revision: Union[str, None] = "20260216_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    1. content 테이블에 user_id 컬럼 추가 (nullable=True로 먼저 추가)
    2. 기존 데이터를 'nature' 계정에 할당
    3. nullable=False로 제약조건 변경
    4. Foreign Key 및 인덱스 추가
    """
    conn = op.get_bind()

    # 1. user_id 컬럼 추가 (nullable=True)
    op.add_column(
        'content',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True)
    )

    # 2. 기존 content 레코드 수 확인
    result = conn.execute(text("SELECT COUNT(*) FROM content WHERE user_id IS NULL"))
    null_count = result.scalar()

    if null_count > 0:
        # 기존 content 레코드가 있으면 'nature' 계정 조회
        result = conn.execute(
            text("SELECT id FROM \"user\" WHERE email = 'nature@timblo.io' LIMIT 1")
        )
        nature_user = result.fetchone()

        if nature_user:
            nature_user_id = nature_user[0]
            # 기존 content 레코드를 nature 계정에 할당
            conn.execute(
                text("UPDATE content SET user_id = :user_id WHERE user_id IS NULL"),
                {"user_id": str(nature_user_id)}
            )
            print(f"✅ Assigned {null_count} existing content records to user: {nature_user_id}")
        else:
            # nature 계정이 없으면 모든 NULL content를 삭제
            print(f"⚠️  WARNING: 'nature@timblo.io' user not found.")
            print(f"    Deleting {null_count} content records with NULL user_id...")
            conn.execute(text("DELETE FROM content WHERE user_id IS NULL"))
            print(f"✅ Deleted {null_count} orphaned content records.")
    else:
        print("✅ No existing content records to migrate.")

    # 3. nullable=False로 제약조건 변경
    op.alter_column('content', 'user_id', nullable=False)

    # 4. 인덱스 추가
    op.create_index('ix_content_user_id', 'content', ['user_id'])

    # 5. Foreign Key 추가
    op.create_foreign_key(
        'fk_content_user_id',
        'content',
        'user',
        ['user_id'],
        ['id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    """user_id 관련 제약조건 및 컬럼 제거"""
    op.drop_constraint('fk_content_user_id', 'content', type_='foreignkey')
    op.drop_index('ix_content_user_id', 'content')
    op.drop_column('content', 'user_id')
