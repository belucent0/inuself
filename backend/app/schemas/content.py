from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from ..db.models import ContentStatus


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
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class ContentListItem(ContentBaseSchema):
    pass


class ContentDetail(ContentBaseSchema):
    transcription: dict[str, Any]
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

