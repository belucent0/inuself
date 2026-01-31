"""extend content table and add file relationships

Revision ID: 20260131_01
Revises: 20251209_01
Create Date: 2026-01-31

Phase 1.5: Content 테이블 확장 및 File 관계 설정
- Content 테이블에 file_id FK, updated_at, completed_at 컬럼 추가
- File 테이블에 size_bytes, mime_type 컬럼 추가
- Transcription, Document에 content_id FK 추가
- 기존 Content 데이터를 File로 마이그레이션
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260131_01'
down_revision: Union[str, None] = '20251209_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Content 테이블에 신규 컬럼 추가
    op.add_column('content', sa.Column('file_id', sa.Integer(), nullable=True))
    op.add_column('content', sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('content', sa.Column('completed_at', sa.TIMESTAMP(timezone=True), nullable=True))

    # file_id FK 및 인덱스 생성
    op.create_foreign_key(
        'fk_content_file_id', 'content', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index('ix_content_file_id', 'content', ['file_id'], unique=True)

    # 2. File 테이블에 신규 컬럼 추가
    op.add_column('file', sa.Column('size_bytes', sa.BigInteger(), nullable=True))
    op.add_column('file', sa.Column('mime_type', sa.String(128), nullable=True))

    # 3. Transcription에 content_id 컬럼 추가
    op.add_column('transcription', sa.Column('content_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_transcription_content_id', 'transcription', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index('ix_transcription_content_id', 'transcription', ['content_id'], unique=True)

    # 4. Document에 content_id 컬럼 추가
    op.add_column('document', sa.Column('content_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_document_content_id', 'document', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index('ix_document_content_id', 'document', ['content_id'], unique=True)

    # 5. 기존 Content 데이터 -> File + Content 매핑
    # 기존 Content 레코드에서 File 생성 (아직 file_id가 없는 레코드)
    # contentstatus -> filestatus 캐스팅 필요 (PostgreSQL enum 타입 불일치)
    op.execute("""
        INSERT INTO file (filename, object_key, content_type, status, title, summary_md, created_at)
        SELECT
            filename,
            object_key,
            'AUDIO'::contenttype,
            status::text::filestatus,
            title,
            summary_md,
            created_at
        FROM content
        WHERE file_id IS NULL AND object_key IS NOT NULL
    """)

    # Content.file_id 업데이트 (object_key로 매칭)
    op.execute("""
        UPDATE content c
        SET file_id = f.id,
            updated_at = c.created_at
        FROM file f
        WHERE c.object_key = f.object_key
          AND c.file_id IS NULL
    """)

    # 6. Transcription.content_id 채우기 (file_id로 매칭)
    op.execute("""
        UPDATE transcription t
        SET content_id = c.id
        FROM content c
        WHERE t.file_id = c.file_id
          AND t.content_id IS NULL
    """)

    # 7. Document.content_id 채우기 (file_id로 매칭)
    op.execute("""
        UPDATE document d
        SET content_id = c.id
        FROM content c
        WHERE d.file_id = c.file_id
          AND d.content_id IS NULL
    """)

    # 8. Transcription, Document의 file_id 제약 완화 (nullable로 변경)
    # unique constraint 확인 후 제거
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transcription_file_id_key') THEN
                ALTER TABLE transcription DROP CONSTRAINT transcription_file_id_key;
            END IF;
        END $$;
    """)
    op.alter_column('transcription', 'file_id', nullable=True)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_file_id_key') THEN
                ALTER TABLE document DROP CONSTRAINT document_file_id_key;
            END IF;
        END $$;
    """)
    op.alter_column('document', 'file_id', nullable=True)

    # 9. Content.filename, Content.object_key nullable로 변경 (이미 File에 있으므로)
    op.alter_column('content', 'filename', nullable=True)
    op.alter_column('content', 'object_key', nullable=True)
    op.alter_column('content', 'transcription', nullable=True)


def downgrade() -> None:
    # Content.filename, Content.object_key not null 복원
    op.alter_column('content', 'transcription', nullable=False)
    op.alter_column('content', 'object_key', nullable=False)
    op.alter_column('content', 'filename', nullable=False)

    # Transcription, Document file_id 제약 복원
    op.alter_column('document', 'file_id', nullable=False)
    op.create_unique_constraint('document_file_id_key', 'document', ['file_id'])

    op.alter_column('transcription', 'file_id', nullable=False)
    op.create_unique_constraint('transcription_file_id_key', 'transcription', ['file_id'])

    # Document의 content_id 제거
    op.drop_index('ix_document_content_id', 'document')
    op.drop_constraint('fk_document_content_id', 'document', type_='foreignkey')
    op.drop_column('document', 'content_id')

    # Transcription의 content_id 제거
    op.drop_index('ix_transcription_content_id', 'transcription')
    op.drop_constraint('fk_transcription_content_id', 'transcription', type_='foreignkey')
    op.drop_column('transcription', 'content_id')

    # File 테이블의 신규 컬럼 제거
    op.drop_column('file', 'mime_type')
    op.drop_column('file', 'size_bytes')

    # Content 테이블의 신규 컬럼 제거
    op.drop_index('ix_content_file_id', 'content')
    op.drop_constraint('fk_content_file_id', 'content', type_='foreignkey')
    op.drop_column('content', 'completed_at')
    op.drop_column('content', 'updated_at')
    op.drop_column('content', 'file_id')
