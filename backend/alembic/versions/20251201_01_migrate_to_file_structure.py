"""migrate to file structure

Revision ID: 20251201_01
Revises: 20251129_01
Create Date: 2025-12-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20251201_01'
down_revision: Union[str, None] = '20251129_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. File 테이블 생성
    op.create_table(
        'file',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=512), nullable=False),
        sa.Column('object_key', sa.String(length=1024), nullable=False),
        sa.Column('content_type', sa.Enum('AUDIO', 'DOCUMENT', name='contenttype'), nullable=False),
        sa.Column('status', sa.Enum('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARIZING', 'COMPLETED', 'ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED', 'CANCELLED', name='filestatus'), nullable=False, server_default='QUEUED'),
        sa.Column('summary_md', sa.Text(), nullable=True),
        sa.Column('title', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_file_id', 'file', ['id'])
    op.create_index('ix_file_content_type', 'file', ['content_type'])
    op.create_index('ix_file_status', 'file', ['status'])

    # 2. Transcription 테이블 생성
    op.create_table(
        'transcription',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('file_id', sa.Integer(), nullable=False),
        sa.Column('speakers', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('duration_seconds', sa.Float(), nullable=False, server_default='0'),
        sa.Column('transcription', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.ForeignKeyConstraint(['file_id'], ['file.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('file_id')
    )
    op.create_index('ix_transcription_id', 'transcription', ['id'])
    op.create_index('ix_transcription_file_id', 'transcription', ['file_id'])

    # 3. Document 테이블 생성
    op.create_table(
        'document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('file_id', sa.Integer(), nullable=False),
        sa.Column('ocr_text', sa.Text(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ocr_metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.ForeignKeyConstraint(['file_id'], ['file.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('file_id')
    )
    op.create_index('ix_document_id', 'document', ['id'])
    op.create_index('ix_document_file_id', 'document', ['file_id'])

    # 4. SttLog에 file_id 컬럼 추가
    op.add_column('stt_log', sa.Column('file_id', sa.Integer(), nullable=True))
    op.create_index('ix_stt_log_file_id', 'stt_log', ['file_id'])
    op.create_foreign_key('fk_stt_log_file_id', 'stt_log', 'file', ['file_id'], ['id'], ondelete='CASCADE')

    # 5. LlmLog에 file_id 컬럼 추가
    op.add_column('llm_log', sa.Column('file_id', sa.Integer(), nullable=True))
    op.create_index('ix_llm_log_file_id', 'llm_log', ['file_id'])
    op.create_foreign_key('fk_llm_log_file_id', 'llm_log', 'file', ['file_id'], ['id'], ondelete='CASCADE')

    # 6. 기존 Content 데이터를 File + Transcription으로 마이그레이션
    # SQL을 사용하여 데이터 마이그레이션
    connection = op.get_bind()
    
    # Content 테이블의 모든 데이터를 File + Transcription으로 복사
    connection.execute(sa.text("""
        INSERT INTO file (id, filename, object_key, content_type, status, summary_md, title, created_at)
        SELECT 
            id,
            filename,
            object_key,
            'AUDIO'::contenttype as content_type,
            status::text::filestatus as status,
            summary_md,
            title,
            created_at
        FROM content
    """))
    
    # Transcription 데이터 복사
    connection.execute(sa.text("""
        INSERT INTO transcription (file_id, speakers, duration_seconds, transcription)
        SELECT 
            id as file_id,
            speakers,
            duration_seconds,
            transcription
        FROM content
    """))
    
    # SttLog의 content_id를 file_id로 복사 (기존 content_id는 유지)
    connection.execute(sa.text("""
        UPDATE stt_log
        SET file_id = content_id
        WHERE content_id IS NOT NULL
    """))
    
    # LlmLog의 content_id를 file_id로 복사 (기존 content_id는 유지)
    connection.execute(sa.text("""
        UPDATE llm_log
        SET file_id = content_id
        WHERE content_id IS NOT NULL
    """))


def downgrade() -> None:
    # 데이터 마이그레이션 역순 (File + Transcription -> Content)
    connection = op.get_bind()
    
    # File과 Transcription을 Content로 복원 (기존 content 테이블이 있으면 스킵)
    connection.execute(sa.text("""
        INSERT INTO content (id, filename, object_key, duration_seconds, transcription, summary_md, title, status, created_at, speakers)
        SELECT 
            f.id,
            f.filename,
            f.object_key,
            COALESCE(t.duration_seconds, 0.0) as duration_seconds,
            COALESCE(t.transcription, '{}'::jsonb) as transcription,
            f.summary_md,
            f.title,
            f.status::text::contentstatus as status,
            f.created_at,
            COALESCE(t.speakers, ARRAY[]::text[]) as speakers
        FROM file f
        LEFT JOIN transcription t ON f.id = t.file_id
        WHERE f.content_type = 'AUDIO'
        ON CONFLICT (id) DO NOTHING
    """))
    
    # SttLog, LlmLog의 file_id를 content_id로 복원 (file_id가 있으면)
    connection.execute(sa.text("""
        UPDATE stt_log
        SET content_id = file_id
        WHERE file_id IS NOT NULL AND content_id IS NULL
    """))
    
    connection.execute(sa.text("""
        UPDATE llm_log
        SET content_id = file_id
        WHERE file_id IS NOT NULL AND content_id IS NULL
    """))
    
    # 외래키 제거
    op.drop_constraint('fk_llm_log_file_id', 'llm_log', type_='foreignkey')
    op.drop_index('ix_llm_log_file_id', table_name='llm_log')
    op.drop_column('llm_log', 'file_id')
    
    op.drop_constraint('fk_stt_log_file_id', 'stt_log', type_='foreignkey')
    op.drop_index('ix_stt_log_file_id', table_name='stt_log')
    op.drop_column('stt_log', 'file_id')
    
    # 테이블 삭제
    op.drop_index('ix_document_file_id', table_name='document')
    op.drop_index('ix_document_id', table_name='document')
    op.drop_table('document')
    
    op.drop_index('ix_transcription_file_id', table_name='transcription')
    op.drop_index('ix_transcription_id', table_name='transcription')
    op.drop_table('transcription')
    
    op.drop_index('ix_file_status', table_name='file')
    op.drop_index('ix_file_content_type', table_name='file')
    op.drop_index('ix_file_id', table_name='file')
    op.drop_table('file')
    
    # Enum 타입 삭제
    op.execute('DROP TYPE IF EXISTS filestatus')
    op.execute('DROP TYPE IF EXISTS contenttype')

