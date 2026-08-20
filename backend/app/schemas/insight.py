"""영상 인사이트 글 스키마."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InsightPostSource(BaseModel):
    id: UUID
    title: str
    filename: str
    media_url: str | None = None
    source_url: str | None = None
    summary_md: str | None = None
    transcript_text: str | None = None
    duration_seconds: float = 0.0
    speakers: list[str] = Field(default_factory=list)


class InsightEvidenceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: str
    title: str
    url: str | None = None
    snippet: str | None = None
    quote_text: str | None = None
    timestamp_seconds: float | None = None
    reliability_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InsightAnnotationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    anchor_text: str
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None
    created_at: datetime


class InsightPostListItem(BaseModel):
    id: UUID
    source_file_id: UUID
    source_title: str
    title: str
    subtitle: str | None = None
    post_type: str
    tone: str
    status: str
    evidence_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class InsightPostDetail(InsightPostListItem):
    body_md: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: InsightPostSource | None = None
    evidences: list[InsightEvidenceSchema] = Field(default_factory=list)
    annotations: list[InsightAnnotationSchema] = Field(default_factory=list)


class InsightPostListResponse(BaseModel):
    items: list[InsightPostListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class InsightPostCreateRequest(BaseModel):
    post_type: str = Field("insight", description="insight, review, lecture_note 등")
    tone: str = Field("analytical", description="analytical, critical, concise 등")
    target_length: str = Field("medium", description="short, medium, long")
    include_transcript_quotes: bool = True
    include_research_prompts: bool = True
    allow_fallback: bool = Field(
        False,
        description="Allow a non-LLM draft only for local debugging when the AI gateway is unavailable.",
    )


class InsightPostUpdateRequest(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    body_md: str | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


class InsightResearchRequest(BaseModel):
    query: str | None = Field(None, description="비워두면 글 제목/메타데이터 기반으로 조사")
    max_results: int = Field(5, ge=1, le=10)
    append_to_body: bool = True
