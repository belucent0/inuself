"""File 스키마."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from ..db.models import FileStatus, ContentType
from .content import SttLogSchema, LlmLogSchema


class TranscriptionSchema(BaseModel):
    """Transcription 스키마."""

    id: int
    file_id: int
    speakers: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    transcription: dict[str, Any]

    class Config:
        from_attributes = True


class DocumentSchema(BaseModel):
    """Document 스키마."""

    id: int
    file_id: int
    ocr_text: str
    page_count: int = 0
    ocr_metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class FileBaseSchema(BaseModel):
    """File 기본 스키마."""

    id: int
    filename: str
    object_key: str
    media_url: str | None = None
    content_type: ContentType
    status: FileStatus
    summary_md: str | None = None
    title: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class FileListItem(FileBaseSchema):
    """파일 목록 항목."""

    # 타입별 정보 (선택적)
    transcription: TranscriptionSchema | None = None
    document: DocumentSchema | None = None


class FileDetail(FileBaseSchema):
    """파일 상세 정보."""

    transcription: TranscriptionSchema | None = None
    document: DocumentSchema | None = None
    logs: list[SttLogSchema] = Field(default_factory=list)
    llm_logs: list[LlmLogSchema] = Field(default_factory=list)


class FileListResponse(BaseModel):
    """파일 목록 응답."""

    items: list[FileListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class FileUploadResponse(BaseModel):
    """파일 업로드 응답."""

    file_id: int
    queued: bool = True

