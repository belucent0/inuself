"""create missing content records for orphan files

Revision ID: 20260131_07
Revises: 20260131_06
Create Date: 2026-01-31

Content가 없는 File에 대해 Content 레코드를 생성합니다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260131_07'
down_revision: Union[str, None] = '20260131_06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Content가 없는 File에 대해 Content 레코드 생성
    # public_id도 함께 생성 (NOT NULL 제약)
    op.execute("""
        INSERT INTO content (id, file_id, status, created_at, updated_at, public_id)
        SELECT
            gen_random_uuid(),
            f.id,
            'QUEUED'::filestatus,
            f.created_at,
            f.created_at,
            gen_random_uuid()
        FROM file f
        LEFT JOIN content c ON c.file_id = f.id
        WHERE c.id IS NULL
    """)


def downgrade() -> None:
    # 생성된 Content 레코드 삭제 (file_id가 있고 status가 QUEUED인 것만)
    # 주의: 이미 처리된 Content는 삭제되지 않음
    op.execute("""
        DELETE FROM content c
        WHERE c.status = 'QUEUED'
        AND NOT EXISTS (
            SELECT 1 FROM transcription t WHERE t.content_id = c.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM document d WHERE d.content_id = c.id
        )
    """)
