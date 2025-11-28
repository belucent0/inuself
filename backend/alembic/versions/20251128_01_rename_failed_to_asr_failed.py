"""refactor: rename FAILED to ASR_FAILED and remove RETRYING status

Revision ID: 20251128_01
Revises: 20251126_01_add_llm_summary_fields
Create Date: 2025-11-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251128_01'
down_revision: Union[str, None] = '20251126_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    1. FAILED 상태를 ASR_FAILED로 변경
    2. RETRYING 상태 제거 (사용되지 않음, 있다면 QUEUED로 변경)
    """
    # PostgreSQL의 enum 타입 변경
    # 먼저 enum 타입을 변경한 다음 데이터를 업데이트해야 함
    
    # 1. 기본값 제거 (enum 타입 변경 전에 필요)
    op.execute("ALTER TABLE content ALTER COLUMN status DROP DEFAULT")
    
    # 2. enum 타입 재생성
    # PostgreSQL에서 enum 값 변경은 복잡하므로 새로운 enum을 만들고 교체
    op.execute("ALTER TYPE contentstatus RENAME TO contentstatus_old")
    
    # 새로운 enum 타입 생성 (ASR_FAILED 포함)
    op.execute("""
        CREATE TYPE contentstatus AS ENUM (
            'QUEUED',
            'PROCESSING',
            'SUMMARIZING',
            'COMPLETED',
            'ASR_FAILED',
            'SUMMARY_FAILED',
            'CANCELLED'
        )
    """)
    
    # 3. 컬럼 타입 변경 (임시로 텍스트로 변환)
    op.execute("""
        ALTER TABLE content 
        ALTER COLUMN status TYPE contentstatus 
        USING status::text::contentstatus
    """)
    
    # 4. 기본값 복원
    op.execute("ALTER TABLE content ALTER COLUMN status SET DEFAULT 'QUEUED'")
    
    # 5. 기존 FAILED 값을 ASR_FAILED로 업데이트 (이제 enum에 ASR_FAILED가 있음)
    op.execute("UPDATE content SET status = 'ASR_FAILED' WHERE status::text = 'FAILED'")
    
    # 6. RETRYING 상태가 있다면 QUEUED로 변경 (혹시 모를 경우 대비)
    op.execute("UPDATE content SET status = 'QUEUED' WHERE status::text = 'RETRYING'")
    
    # 7. 기존 enum 타입 삭제
    op.execute("DROP TYPE contentstatus_old")


def downgrade() -> None:
    """
    1. ASR_FAILED 상태를 FAILED로 복원
    2. RETRYING 상태 추가
    """
    # 1. ASR_FAILED 값을 FAILED로 복원
    op.execute("UPDATE content SET status = 'FAILED' WHERE status = 'ASR_FAILED'")
    
    # 2. enum 타입 재생성
    op.execute("ALTER TYPE contentstatus RENAME TO contentstatus_old")
    
    # 기존 enum 타입 생성 (FAILED, RETRYING 포함)
    op.execute("""
        CREATE TYPE contentstatus AS ENUM (
            'QUEUED',
            'PROCESSING',
            'SUMMARIZING',
            'COMPLETED',
            'FAILED',
            'SUMMARY_FAILED',
            'CANCELLED',
            'RETRYING'
        )
    """)
    
    # 컬럼 타입 변경
    op.execute("""
        ALTER TABLE content 
        ALTER COLUMN status TYPE contentstatus 
        USING status::text::contentstatus
    """)
    
    # 기존 enum 타입 삭제
    op.execute("DROP TYPE contentstatus_old")

