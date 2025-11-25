import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, String, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ContentStatus(str, enum.Enum):
    """콘텐츠 처리 상태."""

    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)
    PROCESSING = "PROCESSING"  # 처리중 (ASR/화자분리 진행 중)
    COMPLETED = "COMPLETED"  # 완료
    FAILED = "FAILED"  # 에러/실패
    CANCELLED = "CANCELLED"  # 취소됨 (사용자 취소 또는 타임아웃)
    RETRYING = "RETRYING"  # 재시도 중 (실패 후 자동 재시도)


class Content(Base):
    """전사 및 화자분리 결과 저장 테이블."""

    __tablename__ = "content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    transcription: Mapped[dict] = mapped_column(JSONB, nullable=False)
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

