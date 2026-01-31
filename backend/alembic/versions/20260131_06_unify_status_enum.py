"""unify status enum types

Revision ID: 20260131_06
Revises: 20260131_05
Create Date: 2026-01-31

PostgreSQL enum 타입 통일:
- filestatus, contentstatus 두 enum을 filestatus로 통일
- Content.status가 filestatus enum을 사용하도록 변경
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260131_06'
down_revision: Union[str, None] = '20260131_05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Content.status의 default 값 제거
    op.alter_column('content', 'status', server_default=None)

    # 2. Content.status를 filestatus enum으로 변환
    op.execute("""
        ALTER TABLE content ALTER COLUMN status TYPE filestatus USING status::text::filestatus
    """)

    # 3. Content.status에 새로운 default 설정
    op.alter_column('content', 'status', server_default="QUEUED")

    # 4. 사용하지 않는 contentstatus enum 타입 삭제
    op.execute("DROP TYPE IF EXISTS contentstatus CASCADE")


def downgrade() -> None:
    # 이 마이그레이션의 다운그레이드는 권장되지 않습니다.
    raise NotImplementedError(
        "이 마이그레이션의 다운그레이드는 지원되지 않습니다."
    )
