from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from ..db.models import ContentStatus, ContentType


class SttLogSchema(BaseModel):
    id: int
    message: str | None = ""
    log: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class LlmLogSchema(BaseModel):
    id: int
    message: str | None = ""
    log: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class ContentBaseSchema(BaseModel):
    id: int
    filename: str
    object_key: str
    media_url: str | None = None
    speakers: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    status: ContentStatus
    summary_md: str | None = None
    title: str | None = None
    created_at: datetime
    updated_at: datetime | None = Field(None)  # File 모델에는 없지만 하위 호환성을 위해 유지
    # 파일 타입 (선택적, 하위 호환성)
    file_type: str | None = None  # "AUDIO" 또는 "DOCUMENT"
    content_type: ContentType | None = None  # ContentType enum

    class Config:
        from_attributes = True


class ContentListItem(ContentBaseSchema):
    # 타입별 콘텐츠 (선택적) - lazy import로 순환 참조 방지
    transcription_content: dict[str, Any] | None = None
    document: dict[str, Any] | None = None


class ContentDetail(ContentBaseSchema):
    transcription: dict[str, Any]
    # 타입별 콘텐츠 (선택적)
    transcription_content: dict[str, Any] | None = None
    document: dict[str, Any] | None = None
    logs: list[SttLogSchema] = Field(default_factory=list)
    llm_logs: list[LlmLogSchema] = Field(default_factory=list)


class UploadResponse(BaseModel):
    content_id: int
    queued: bool = True


class BulkDeleteRequest(BaseModel):
    content_ids: list[int] = Field(..., min_length=1, description="삭제 대상 콘텐츠 ID 목록")


class BulkDeleteResponse(BaseModel):
    deleted_count: int
    deleted_ids: list[int] = Field(default_factory=list)
    skipped_ids: list[int] = Field(default_factory=list)
    message: str


class ContentListResponse(BaseModel):
    items: list[ContentListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ReclusterSpeakersRequest(BaseModel):
    num_speakers: int | None = Field(None, ge=1, description="목표 화자 수 (None이면 자동 결정)")
    similarity_threshold: float = Field(0.7, ge=0.0, le=1.0, description="코사인 유사도 임계값")


class ReclusterSpeakersResponse(BaseModel):
    message: str
    num_speakers: int
    speaker_labels: list[str]
    updated_segments_count: int


class YouTubeUploadRequest(BaseModel):
    """YouTube URL 업로드 요청"""
    url: str = Field(..., description="YouTube 영상 URL")


class YouTubeUploadResponse(BaseModel):
    """YouTube URL 업로드 응답"""
    content_id: int
    queued: bool = True
    message: str = "YouTube 다운로드가 시작되었습니다"

