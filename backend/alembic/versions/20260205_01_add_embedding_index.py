"""Add pgvector column and indexes for content embeddings.

Revision ID: 20260205_01
Revises: 20260201_02
Create Date: 2026-02-05
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260205_01"
down_revision = "20260201_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Fresh databases need the column before the index can be created. Existing
    # databases that already have the column keep their data.
    op.execute("""
        ALTER TABLE content
        ADD COLUMN IF NOT EXISTS embedding vector(768)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_embedding_cosine
        ON content USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 32)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_embedding_not_null
        ON content (id)
        WHERE embedding IS NOT NULL AND status = 'COMPLETED'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_content_embedding_not_null")
    op.execute("DROP INDEX IF EXISTS idx_content_embedding_cosine")
    op.execute("ALTER TABLE content DROP COLUMN IF EXISTS embedding")
