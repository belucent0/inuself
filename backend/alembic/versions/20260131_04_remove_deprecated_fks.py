"""remove deprecated file_id foreign keys

Revision ID: 20260131_04
Revises: 20260131_03
Create Date: 2026-01-31

Phase 3: Deprecated FK 및 컬럼 제거
- File 테이블: status, title, summary_md 컬럼 제거
- Transcription 테이블: file_id FK 제거
- Document 테이블: file_id FK 제거
- SttLog 테이블: file_id FK 제거
- LlmLog 테이블: file_id FK 제거
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260131_04'
down_revision: Union[str, None] = '20260131_03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. File 테이블에서 deprecated 컬럼 제거 (있으면)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_file_status') THEN
                DROP INDEX ix_file_status;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'file' AND column_name = 'status') THEN
                ALTER TABLE file DROP COLUMN status;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'file' AND column_name = 'title') THEN
                ALTER TABLE file DROP COLUMN title;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'file' AND column_name = 'summary_md') THEN
                ALTER TABLE file DROP COLUMN summary_md;
            END IF;
        END $$;
    """)

    # 2. Transcription 테이블에서 file_id FK 제거 (있으면)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transcription_file_id_fkey') THEN
                ALTER TABLE transcription DROP CONSTRAINT transcription_file_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_transcription_file_id') THEN
                DROP INDEX ix_transcription_file_id;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'transcription' AND column_name = 'file_id') THEN
                ALTER TABLE transcription DROP COLUMN file_id;
            END IF;
        END $$;
    """)

    # 3. Document 테이블에서 file_id FK 제거 (있으면)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_file_id_fkey') THEN
                ALTER TABLE document DROP CONSTRAINT document_file_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_document_file_id') THEN
                DROP INDEX ix_document_file_id;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'document' AND column_name = 'file_id') THEN
                ALTER TABLE document DROP COLUMN file_id;
            END IF;
        END $$;
    """)

    # 4. SttLog 테이블에서 file_id FK 제거 (있으면)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stt_log_file_id_fkey') THEN
                ALTER TABLE stt_log DROP CONSTRAINT stt_log_file_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_stt_log_file_id') THEN
                DROP INDEX ix_stt_log_file_id;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'stt_log' AND column_name = 'file_id') THEN
                ALTER TABLE stt_log DROP COLUMN file_id;
            END IF;
        END $$;
    """)

    # 5. LlmLog 테이블에서 file_id FK 제거 (있으면)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'llm_log_file_id_fkey') THEN
                ALTER TABLE llm_log DROP CONSTRAINT llm_log_file_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_llm_log_file_id') THEN
                DROP INDEX ix_llm_log_file_id;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'llm_log' AND column_name = 'file_id') THEN
                ALTER TABLE llm_log DROP COLUMN file_id;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # 5. LlmLog 테이블에 file_id FK 복원
    op.add_column(
        'llm_log',
        sa.Column('file_id', sa.Integer, nullable=True)
    )
    op.create_index('ix_llm_log_file_id', 'llm_log', ['file_id'])
    op.create_foreign_key(
        'llm_log_file_id_fkey', 'llm_log', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )

    # 4. SttLog 테이블에 file_id FK 복원
    op.add_column(
        'stt_log',
        sa.Column('file_id', sa.Integer, nullable=True)
    )
    op.create_index('ix_stt_log_file_id', 'stt_log', ['file_id'])
    op.create_foreign_key(
        'stt_log_file_id_fkey', 'stt_log', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )

    # 3. Document 테이블에 file_id FK 복원
    op.add_column(
        'document',
        sa.Column('file_id', sa.Integer, nullable=True)
    )
    op.create_index('ix_document_file_id', 'document', ['file_id'])
    op.create_foreign_key(
        'document_file_id_fkey', 'document', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )

    # 2. Transcription 테이블에 file_id FK 복원
    op.add_column(
        'transcription',
        sa.Column('file_id', sa.Integer, nullable=True)
    )
    op.create_index('ix_transcription_file_id', 'transcription', ['file_id'])
    op.create_foreign_key(
        'transcription_file_id_fkey', 'transcription', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )

    # 1. File 테이블에 deprecated 컬럼 복원
    op.add_column(
        'file',
        sa.Column('summary_md', sa.Text, nullable=True)
    )
    op.add_column(
        'file',
        sa.Column('title', sa.String(512), nullable=True)
    )
    op.add_column(
        'file',
        sa.Column(
            'status',
            postgresql.ENUM('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARY_QUEUED',
                          'SUMMARIZING', 'COMPLETED', 'ASR_FAILED', 'OCR_FAILED',
                          'SUMMARY_FAILED', 'CANCELLED', name='filestatus', create_type=False),
            server_default='QUEUED',
            nullable=False
        )
    )
    op.create_index('ix_file_status', 'file', ['status'])
