"""convert all primary keys and foreign keys to UUID v7

Revision ID: 20260131_05
Revises: 20260131_04
Create Date: 2026-01-31

Phase 4: UUID v7 PK 전환
- 모든 테이블의 PK를 Integer에서 UUID v7으로 전환
- 모든 FK도 UUID로 전환
- 기존 데이터에 UUID 할당 (gen_random_uuid 기반 순차 생성)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260131_05'
down_revision: Union[str, None] = '20260131_04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL uuid-ossp 확장 활성화
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ========== 1. File 테이블 UUID 전환 ==========
    # 1.1 UUID 컬럼 추가
    op.add_column('file', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))

    # 1.2 기존 레코드에 UUID 생성
    op.execute("UPDATE file SET uuid = gen_random_uuid()")

    # 1.3 UUID 컬럼 NOT NULL + UNIQUE
    op.alter_column('file', 'uuid', nullable=False)
    op.create_unique_constraint('uq_file_uuid', 'file', ['uuid'])

    # ========== 2. Content 테이블 UUID 전환 ==========
    # 2.1 UUID 컬럼 추가 (id용, file_id용)
    op.add_column('content', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('content', sa.Column('file_uuid', postgresql.UUID(as_uuid=True), nullable=True))

    # 2.2 기존 레코드에 UUID 생성
    op.execute("UPDATE content SET uuid = gen_random_uuid()")

    # 2.3 file_uuid 연결 (기존 file_id -> File.uuid)
    op.execute("""
        UPDATE content c
        SET file_uuid = f.uuid
        FROM file f
        WHERE c.file_id = f.id
    """)

    # 2.4 UUID 컬럼 NOT NULL + UNIQUE
    op.alter_column('content', 'uuid', nullable=False)
    op.create_unique_constraint('uq_content_uuid', 'content', ['uuid'])

    # ========== 3. Transcription 테이블 UUID 전환 ==========
    op.add_column('transcription', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('transcription', sa.Column('content_uuid', postgresql.UUID(as_uuid=True), nullable=True))

    op.execute("UPDATE transcription SET uuid = gen_random_uuid()")
    op.execute("""
        UPDATE transcription t
        SET content_uuid = c.uuid
        FROM content c
        WHERE t.content_id = c.id
    """)

    op.alter_column('transcription', 'uuid', nullable=False)
    op.create_unique_constraint('uq_transcription_uuid', 'transcription', ['uuid'])

    # ========== 4. Document 테이블 UUID 전환 ==========
    op.add_column('document', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('document', sa.Column('content_uuid', postgresql.UUID(as_uuid=True), nullable=True))

    op.execute("UPDATE document SET uuid = gen_random_uuid()")
    op.execute("""
        UPDATE document d
        SET content_uuid = c.uuid
        FROM content c
        WHERE d.content_id = c.id
    """)

    op.alter_column('document', 'uuid', nullable=False)
    op.create_unique_constraint('uq_document_uuid', 'document', ['uuid'])

    # ========== 5. SttLog 테이블 UUID 전환 ==========
    op.add_column('stt_log', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('stt_log', sa.Column('content_uuid', postgresql.UUID(as_uuid=True), nullable=True))

    op.execute("UPDATE stt_log SET uuid = gen_random_uuid()")
    op.execute("""
        UPDATE stt_log s
        SET content_uuid = c.uuid
        FROM content c
        WHERE s.content_id = c.id
    """)

    op.alter_column('stt_log', 'uuid', nullable=False)
    op.create_unique_constraint('uq_stt_log_uuid', 'stt_log', ['uuid'])

    # ========== 6. LlmLog 테이블 UUID 전환 ==========
    op.add_column('llm_log', sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('llm_log', sa.Column('content_uuid', postgresql.UUID(as_uuid=True), nullable=True))

    op.execute("UPDATE llm_log SET uuid = gen_random_uuid()")
    op.execute("""
        UPDATE llm_log l
        SET content_uuid = c.uuid
        FROM content c
        WHERE l.content_id = c.id
    """)

    op.alter_column('llm_log', 'uuid', nullable=False)
    op.create_unique_constraint('uq_llm_log_uuid', 'llm_log', ['uuid'])

    # ========== 7. 기존 FK 제약조건 삭제 ==========
    # Content.file_id FK
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_content_file_id') THEN
                ALTER TABLE content DROP CONSTRAINT fk_content_file_id;
            END IF;
        END $$;
    """)
    op.drop_index('ix_content_file_id', table_name='content')

    # Transcription.content_id FK
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_transcription_content_id') THEN
                ALTER TABLE transcription DROP CONSTRAINT fk_transcription_content_id;
            END IF;
        END $$;
    """)
    op.drop_index('ix_transcription_content_id', table_name='transcription')

    # Document.content_id FK
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_document_content_id') THEN
                ALTER TABLE document DROP CONSTRAINT fk_document_content_id;
            END IF;
        END $$;
    """)
    op.drop_index('ix_document_content_id', table_name='document')

    # SttLog.content_id FK
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stt_log_content_id_fkey') THEN
                ALTER TABLE stt_log DROP CONSTRAINT stt_log_content_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("DROP INDEX IF EXISTS ix_stt_log_content_id")

    # LlmLog.content_id FK
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'llm_log_content_id_fkey') THEN
                ALTER TABLE llm_log DROP CONSTRAINT llm_log_content_id_fkey;
            END IF;
        END $$;
    """)
    op.execute("DROP INDEX IF EXISTS ix_llm_log_content_id")

    # ========== 8. 기존 Integer PK 제거 및 UUID PK 승격 ==========

    # 8.1 File 테이블
    op.drop_constraint('file_pkey', 'file', type_='primary')
    op.drop_column('file', 'id')
    op.execute("ALTER TABLE file RENAME COLUMN uuid TO id")
    op.create_primary_key('file_pkey', 'file', ['id'])
    op.drop_constraint('uq_file_uuid', 'file', type_='unique')

    # 8.2 Content 테이블
    op.drop_constraint('content_pkey', 'content', type_='primary')
    op.drop_column('content', 'id')
    op.drop_column('content', 'file_id')
    op.execute("ALTER TABLE content RENAME COLUMN uuid TO id")
    op.execute("ALTER TABLE content RENAME COLUMN file_uuid TO file_id")
    op.create_primary_key('content_pkey', 'content', ['id'])
    op.drop_constraint('uq_content_uuid', 'content', type_='unique')
    op.create_index('ix_content_file_id', 'content', ['file_id'], unique=True)
    op.create_foreign_key(
        'fk_content_file_id', 'content', 'file',
        ['file_id'], ['id'], ondelete='CASCADE'
    )

    # 8.3 Transcription 테이블
    op.drop_constraint('transcription_pkey', 'transcription', type_='primary')
    op.drop_column('transcription', 'id')
    op.drop_column('transcription', 'content_id')
    op.execute("ALTER TABLE transcription RENAME COLUMN uuid TO id")
    op.execute("ALTER TABLE transcription RENAME COLUMN content_uuid TO content_id")
    op.create_primary_key('transcription_pkey', 'transcription', ['id'])
    op.drop_constraint('uq_transcription_uuid', 'transcription', type_='unique')
    op.create_index('ix_transcription_content_id', 'transcription', ['content_id'], unique=True)
    op.create_foreign_key(
        'fk_transcription_content_id', 'transcription', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )

    # 8.4 Document 테이블
    op.drop_constraint('document_pkey', 'document', type_='primary')
    op.drop_column('document', 'id')
    op.drop_column('document', 'content_id')
    op.execute("ALTER TABLE document RENAME COLUMN uuid TO id")
    op.execute("ALTER TABLE document RENAME COLUMN content_uuid TO content_id")
    op.create_primary_key('document_pkey', 'document', ['id'])
    op.drop_constraint('uq_document_uuid', 'document', type_='unique')
    op.create_index('ix_document_content_id', 'document', ['content_id'], unique=True)
    op.create_foreign_key(
        'fk_document_content_id', 'document', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )

    # 8.5 SttLog 테이블
    op.drop_constraint('stt_log_pkey', 'stt_log', type_='primary')
    op.drop_column('stt_log', 'id')
    op.drop_column('stt_log', 'content_id')
    op.execute("ALTER TABLE stt_log RENAME COLUMN uuid TO id")
    op.execute("ALTER TABLE stt_log RENAME COLUMN content_uuid TO content_id")
    op.create_primary_key('stt_log_pkey', 'stt_log', ['id'])
    op.drop_constraint('uq_stt_log_uuid', 'stt_log', type_='unique')
    op.create_index('ix_stt_log_content_id', 'stt_log', ['content_id'])
    op.create_foreign_key(
        'stt_log_content_id_fkey', 'stt_log', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )

    # 8.6 LlmLog 테이블
    op.drop_constraint('llm_log_pkey', 'llm_log', type_='primary')
    op.drop_column('llm_log', 'id')
    op.drop_column('llm_log', 'content_id')
    op.execute("ALTER TABLE llm_log RENAME COLUMN uuid TO id")
    op.execute("ALTER TABLE llm_log RENAME COLUMN content_uuid TO content_id")
    op.create_primary_key('llm_log_pkey', 'llm_log', ['id'])
    op.drop_constraint('uq_llm_log_uuid', 'llm_log', type_='unique')
    op.create_index('ix_llm_log_content_id', 'llm_log', ['content_id'])
    op.create_foreign_key(
        'llm_log_content_id_fkey', 'llm_log', 'content',
        ['content_id'], ['id'], ondelete='CASCADE'
    )


def downgrade() -> None:
    """
    UUID -> Integer 롤백은 권장되지 않습니다.
    데이터 손실 가능성이 있으며, 새로운 UUID를 기반으로 생성된 데이터는
    Integer 매핑이 불가능합니다.

    필요시 백업 복원을 권장합니다.
    """
    raise NotImplementedError(
        "UUID -> Integer 다운그레이드는 지원되지 않습니다. "
        "데이터 손실 가능성이 있으므로 백업 복원을 권장합니다."
    )
