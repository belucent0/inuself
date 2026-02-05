"""Add pgvector index for embedding column

Revision ID: 20260205_01
Revises: 20260201_02
Create Date: 2026-02-05

Phase 2: Semantic Search
- Add IVFFlat index on content.embedding for fast cosine similarity search
- Add partial index for non-null embeddings with COMPLETED status
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260205_01"
down_revision = "20260201_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector 확장 활성화 (이미 활성화되어 있으면 무시)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # embedding 컬럼에 IVFFlat 인덱스 생성 (코사인 거리)
    # lists 파라미터: 데이터 크기의 sqrt(row_count) 권장
    # 1000개 콘텐츠 기준: lists=32
    # 10000개 기준: lists=100
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_embedding_cosine
        ON content USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 32)
    """)

    # NULL이 아닌 임베딩만 검색하도록 부분 인덱스 추가
    # status='COMPLETED'인 콘텐츠만 검색 대상
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_embedding_not_null
        ON content (id)
        WHERE embedding IS NOT NULL AND status = 'COMPLETED'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_content_embedding_not_null")
    op.execute("DROP INDEX IF EXISTS idx_content_embedding_cosine")
