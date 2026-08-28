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
    Index,
    text,
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
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"  # 외부 소스 다운로드 실패
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
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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

    # User FK (1:N - 사용자별 콘텐츠 분리)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
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
    summary_sections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
    user: Mapped["User"] = relationship("User", back_populates="contents")
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

    # AI 대화 스레드 관계 (1:N - 콘텐츠에 대한 대화들)
    threads: Mapped[list["AiThread"]] = relationship(
        "AiThread", back_populates="content"
    )

    # 사용자 이벤트 관계 (1:N)
    events: Mapped[list["UserEvent"]] = relationship(
        "UserEvent", back_populates="content"
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

    # 콘텐츠 관계 (1:N - 사용자별 콘텐츠 분리)
    contents: Mapped[list["Content"]] = relationship(
        "Content", back_populates="user", cascade="all, delete-orphan"
    )

    # 검사 결과 관계 (1:N - 검사 이력 누적)
    scan_results: Mapped[list["ScanResult"]] = relationship(
        "ScanResult", back_populates="user", cascade="all, delete-orphan", order_by="ScanResult.created_at.desc()"
    )

    # AI 대화 스레드 관계 (1:N)
    threads: Mapped[list["AiThread"]] = relationship(
        "AiThread", back_populates="user", cascade="all, delete-orphan", order_by="AiThread.updated_at.desc()"
    )

    # 사용자 행동 이벤트 관계 (1:N)
    events: Mapped[list["UserEvent"]] = relationship(
        "UserEvent", back_populates="user", cascade="all, delete-orphan"
    )


class ScanResult(Base):
    """범용 심리검사 결과 테이블.

    다양한 검사 유형(WPI, WSI, MCDC 등)의 결과를 단일 테이블에서 관리.
    검사별 상세 데이터는 JSONB(data) 컬럼에 저장.

    WPI 예시:
      scan_type: "wpi"
      status: "completed"
      data: {
        "version": 1,
        "i_test": {"scores": {...}, "dominant_type": "...", "raw_responses": {...}},
        "me_test": {"scores": {...}, "dominant_type": "...", "raw_responses": {...}},
        "gap_analysis": {"axis_gaps": {...}}
      }

    WSI 예시 (향후):
      scan_type: "wsi"
      status: "completed"
      data: {
        "version": 1,
        "scores": {...},
        "raw_responses": {...}
      }
    """

    __tablename__ = "scan_result"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # User FK (1:N - 검사 이력 누적)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 검사 유형 (wpi, wsi, mcdc 등)
    scan_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 검사 상태 (in_progress, completed)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")

    # 검사 데이터 (JSONB) - 검사별 구조 상이, version 필드로 관리
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    user: Mapped["User"] = relationship("User", back_populates="scan_results")


class AiThread(Base):
    """AI 대화 스레드.

    사용자의 대화 세션 (사이드바에 표시되는 단위).
    Redis 캐시 + PostgreSQL 영속 저장 구조.
    """

    __tablename__ = "ai_thread"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # User FK (필수)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content FK (선택 - 특정 콘텐츠에 대한 대화)
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 대화 제목 (첫 메시지 기반 자동 생성)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 메타데이터 (mode, model 등)
    metadata_: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 아카이브 여부 (삭제 대신 아카이브)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    user: Mapped["User"] = relationship("User", back_populates="threads")
    content: Mapped["Content | None"] = relationship("Content", back_populates="threads")
    messages: Mapped[list["AiMessage"]] = relationship(
        "AiMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AiMessage.created_at",
    )
    events: Mapped[list["UserEvent"]] = relationship(
        "UserEvent", back_populates="thread"
    )


class AiMessage(Base):
    """AI 대화 메시지.

    개별 대화 턴 (user/assistant).

    v1.0.0: 메시지 상태 세분화 + 부분 응답 저장
    - queued: 메시지 생성됨, 처리 대기
    - analyzing: 의도 분석 + 검색 전략 수립
    - searching: 웹/RAG 검색 수행
    - thinking: 사고 과정 (reasoning)
    - generating: 답변 토큰 생성
    - completed: 완료
    - failed: 실패
    """

    __tablename__ = "ai_message"
    __table_args__ = (
        Index(
            "uq_ai_message_active_assistant_per_thread",
            "thread_id",
            unique=True,
            postgresql_where=text(
                "role = 'assistant' AND status IN "
                "('queued', 'analyzing', 'searching', 'thinking', 'generating')"
            ),
        ),
    )

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # Thread FK (필수)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_thread.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 메시지 역할 ("user" | "assistant")
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    # 메시지 내용
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 메시지 상태
    # v1.0.0: queued | analyzing | searching | thinking | generating | completed | failed
    status: Mapped[str] = mapped_column(
        String(20),
        default="completed",  # 기존 메시지 호환성
        nullable=False,
        index=True,
    )

    # 부분 응답 (스트리밍 중 2초마다 저장)
    # v1.0.0: SSE 재연결 시 복구용
    partial_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # 메타데이터 (sources, thinking_steps, mode, model 등)
    metadata_: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 관계
    thread: Mapped["AiThread"] = relationship("AiThread", back_populates="messages")


class UserEvent(Base):
    """사용자 행동 이벤트 추적.

    개인화/분석을 위한 행동 데이터 수집.
    event_type: chat_message, content_upload, content_view, feedback, search_query 등
    """

    __tablename__ = "user_event"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # User FK (필수)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 이벤트 유형
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Content FK (선택)
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Thread FK (선택)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_thread.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 이벤트 페이로드 (상세 데이터)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 관계
    user: Mapped["User"] = relationship("User", back_populates="events")
    content: Mapped["Content | None"] = relationship("Content", back_populates="events")
    thread: Mapped["AiThread | None"] = relationship("AiThread", back_populates="events")


class SpeakerProfile(Base):
    """상담자 음성 프로필 저장 테이블.

    Pyannote embedding 기반 화자 매칭을 위한 음성 프로필 저장.
    """

    __tablename__ = "speaker_profile"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # 화자 유형 (counselor / client)
    speaker_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    # 화자 이름
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # 음성 embedding (Pyannote, 512차원)
    voice_embedding: Mapped[list[float]] = mapped_column(
        Vector(512), nullable=False
    )

    # 활성 여부
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    counseling_sessions: Mapped[list["CounselingSession"]] = relationship(
        "CounselingSession",
        back_populates="counselor_profile",
        foreign_keys="CounselingSession.counselor_profile_id",
    )


class Client(Base):
    """내담자 프로필 테이블.

    상담 세션의 내담자 정보 및 누적된 자기표현 프로필.
    """

    __tablename__ = "client"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # 내담자 이름
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # 최신 자기표현 프로필 (JSONB)
    # 구조: { "정체성": [...], "의미/목적": [...], ... }
    latest_profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # 프로필 embedding (768차원 - 최신 세션 기반)
    profile_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )

    # WPI 검사 결과 FK (선택)
    wpi_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("scan_result.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    sessions: Mapped[list["CounselingSession"]] = relationship(
        "CounselingSession", back_populates="client", cascade="all, delete-orphan"
    )


class CounselingSession(Base):
    """상담 세션 기본 정보 및 분석 결과.

    상담 녹취 분석 결과를 저장하는 테이블.
    """

    __tablename__ = "counseling_session"

    # UUID v7 Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )

    # Client FK (필수)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("client.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SpeakerProfile FK (상담자, 선택)
    counselor_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("speaker_profile.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 전사 파일 FK (선택)
    transcription_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 세션 번호 (회차)
    session_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 분석 결과 데이터 (JSONB)
    # 구조:
    # {
    #   "self_expressions": [...],
    #   "situation_awareness": "...",
    #   "problem_recognition": "...",
    #   "quality_score": 85.5,
    #   "speaker_embeddings": {"SPEAKER_00": [...], ...}
    # }
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 세션 embedding (768차원 - Content embedding 기반)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # 관계
    client: Mapped["Client"] = relationship("Client", back_populates="sessions")
    counselor_profile: Mapped["SpeakerProfile | None"] = relationship(
        "SpeakerProfile", back_populates="counseling_sessions", foreign_keys=[counselor_profile_id]
    )
