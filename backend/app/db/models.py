import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, String, TIMESTAMP, Text, Float
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class FileStatus(str, enum.Enum):
    """파일 처리 상태."""

    # 초기 상태
    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)
    
    # 진행 중 상태
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
    """파일 공통 정보 저장 테이블."""

    __tablename__ = "file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType),
        nullable=False,
        index=True,
    )
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus),
        default=FileStatus.QUEUED,
        nullable=False,
        index=True,
    )
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    # 관계
    transcription: Mapped["Transcription | None"] = relationship(
        "Transcription", back_populates="file", uselist=False, cascade="all, delete-orphan"
    )
    document: Mapped["Document | None"] = relationship(
        "Document", back_populates="file", uselist=False, cascade="all, delete-orphan"
    )
    logs: Mapped[list["SttLog"]] = relationship(
        "SttLog", back_populates="file", cascade="all, delete-orphan"
    )
    llm_logs: Mapped[list["LlmLog"]] = relationship(
        "LlmLog", back_populates="file", cascade="all, delete-orphan"
    )


class Transcription(Base):
    """오디오 전사 및 화자분리 결과 저장 테이블."""

    __tablename__ = "transcription"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("file.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    transcription: Mapped[dict] = mapped_column(JSONB, nullable=False)

    file: Mapped["File"] = relationship("File", back_populates="transcription")


class Document(Base):
    """문서 OCR 결과 저장 테이블."""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("file.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)  # Docling HTML 출력 (뷰어용)

    file: Mapped["File"] = relationship("File", back_populates="document")


# 하위 호환성을 위한 Content 클래스 (deprecated)
class Content(Base):
    """전사 및 화자분리 결과 저장 테이블 (deprecated - File 사용 권장)."""

    __tablename__ = "content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    transcription: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus),
        default=ContentStatus.QUEUED,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    logs: Mapped[list["SttLog"]] = relationship(
        "SttLog", back_populates="content", cascade="all, delete-orphan"
    )
    llm_logs: Mapped[list["LlmLog"]] = relationship(
        "LlmLog", back_populates="content", cascade="all, delete-orphan"
    )


class SttLog(Base):
    """처리 과정 로그 테이블."""

    __tablename__ = "stt_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("file.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.id", ondelete="CASCADE"), nullable=True, index=True
    )
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    file: Mapped["File | None"] = relationship("File", back_populates="logs")
    content: Mapped["Content | None"] = relationship("Content", back_populates="logs")


class LlmLog(Base):
    """LLM 요약 과정 로그 테이블."""

    __tablename__ = "llm_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("file.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.id", ondelete="CASCADE"), nullable=True, index=True
    )
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    file: Mapped["File | None"] = relationship("File", back_populates="llm_logs")
    content: Mapped["Content | None"] = relationship("Content", back_populates="llm_logs")

