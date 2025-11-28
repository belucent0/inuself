import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, String, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ContentStatus(str, enum.Enum):
    """콘텐츠 처리 상태."""

    # 초기 상태
    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)
    
    # 진행 중 상태
    PROCESSING = "PROCESSING"  # ASR/화자분리 진행 중
    SUMMARIZING = "SUMMARIZING"  # LLM 요약 중
    
    # 완료 상태
    COMPLETED = "COMPLETED"  # 전체 파이프라인 완료
    
    # 실패 상태
    ASR_FAILED = "ASR_FAILED"  # ASR/화자분리 단계 실패
    SUMMARY_FAILED = "SUMMARY_FAILED"  # LLM 요약 실패
    
    # 취소 상태
    CANCELLED = "CANCELLED"  # 취소됨 (사용자 취소 또는 타임아웃)


class Content(Base):
    """전사 및 화자분리 결과 저장 테이블."""

    __tablename__ = "content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    transcription: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"))
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    content: Mapped[Content] = relationship("Content", back_populates="logs")


class LlmLog(Base):
    """LLM 요약 과정 로그 테이블."""

    __tablename__ = "llm_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"))
    log: Mapped[dict] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    content: Mapped[Content] = relationship("Content", back_populates="llm_logs")

