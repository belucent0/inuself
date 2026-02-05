import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    TIMESTAMP,
    Text,
    Float,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from .base import Base


def generate_uuid7() -> uuid.UUID:
    """UUID v7 생성 (Python 3.13+ 또는 uuid_utils 폴백)."""
    if hasattr(uuid, "uuid7"):
        return uuid.uuid7()
    # Python 3.12 이하: uuid_utils 사용
    try:
        import uuid_utils

        return uuid.UUID(str(uuid_utils.uuid7()))
    except ImportError:
        # 폴백: uuid4 사용 (시간 순서 보장 안 됨)
        return uuid.uuid4()


class FileStatus(str, enum.Enum):
    """파일 처리 상태."""

    # 초기 상태
    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)

    # 진행 중 상태
    PULLING = "PULLING"  # 외부 소스에서 파일 다운로드/가져오기 진행 중
    PROCESSING = "PROCESSING"  # ASR/화자분리 진행 중
    OCR_PROCESSING = "OCR_PROCESSING"  # OCR 처리 중
    SUMMARY_QUEUED = "SUMMARY_QUEUED"  # LLM 요약 대기중 (큐에 등록됨)
    SUMMARIZING = "SUMMARIZING"  # LLM 요약 중

    # 완료 상태
    COMPLETED = "COMPLETED"  # 전체 파이프라인 완료

    # 실패 상태
    ASR_FAILED = "ASR_FAILED"  # ASR/화자분리 단계 실패
    OCR_FAILED = "OCR_FAILED"  # OCR 처리 실패
    SUMMARY_FAILED = "SUMMARY_FAILED"  # LLM 요약 실패

    # 취소 상태
    CANCELLED = "CANCELLED"  # 취소됨 (사용자 취소 또는 타임아웃)


class ContentType(str, enum.Enum):
    """파일 콘텐츠 타입."""

    AUDIO = "AUDIO"  # 오디오 파일
    DOCUMENT = "DOCUMENT"  # 문서 파일
    PORTRAY = "PORTRAY"  # 이미지 묘사


# 하위 호환성을 위한 별칭
ContentStatus = FileStatus


class File(Base):
    """파일 공통 정보 저장 테이블 (불변 메타데이터)."""

    __tablename__ = "file"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType),
        nullable=False,
        index=True,
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 관계
    content: Mapped["Content | None"] = relationship(
        "Content", back_populates="file", uselist=False, cascade="all, delete-orphan"
    )


class Content(Base):
    """콘텐츠 메타데이터 및 처리 결과 저장 테이블.

    File의 가변 데이터(상태, 요약, 제목)를 관리합니다.
    기존 ASR 전용 필드(speakers, duration_seconds, transcription 등)는
    Transcription 테이블로 분리되었습니다.
    """

    __tablename__ = "content"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # File FK (UUID)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("file.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
        index=True,
    )

    # 처리 상태 및 결과
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus),
        default=ContentStatus.QUEUED,
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )  # FLM embeddinggemma:300m (768 dimensions)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    file: Mapped["File | None"] = relationship("File", back_populates="content")
    transcription_result: Mapped["Transcription | None"] = relationship(
        "Transcription",
        back_populates="content",
        uselist=False,
        cascade="all, delete-orphan",
    )
    document_result: Mapped["Document | None"] = relationship(
        "Document",
        back_populates="content",
        uselist=False,
        cascade="all, delete-orphan",
    )
    logs: Mapped[list["SttLog"]] = relationship(
        "SttLog", back_populates="content", cascade="all, delete-orphan"
    )
    llm_logs: Mapped[list["LlmLog"]] = relationship(
        "LlmLog", back_populates="content", cascade="all, delete-orphan"
    )


class Transcription(Base):
    """오디오 전사 및 화자분리 결과 저장 테이블."""

    __tablename__ = "transcription"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
        index=True,
    )
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    transcription: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # 관계
    content: Mapped["Content | None"] = relationship(
        "Content", back_populates="transcription_result"
    )


class Document(Base):
    """문서 OCR 결과 저장 테이블."""

    __tablename__ = "document"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
        index=True,
    )
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 관계
    content: Mapped["Content | None"] = relationship(
        "Content", back_populates="document_result"
    )


class SttLog(Base):
    """처리 과정 로그 테이블."""

    __tablename__ = "stt_log"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 관계
    content: Mapped["Content | None"] = relationship("Content", back_populates="logs")


class LlmLog(Base):
    """LLM 요약 과정 로그 테이블."""

    __tablename__ = "llm_log"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 관계
    content: Mapped["Content | None"] = relationship(
        "Content", back_populates="llm_logs"
    )


def generate_storage_key() -> str:
    """스토리지 네임스페이스 키 생성 (12자 hex)."""
    import secrets

    return secrets.token_hex(6)


class User(Base):
    """사용자 테이블."""

    __tablename__ = "user"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    storage_key: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, default=generate_storage_key
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_super: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
