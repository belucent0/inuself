"""WPI 심리검사 Pydantic 스키마.

WPI(Whang's Personality Inventory) API 요청/응답 스키마 정의.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# === I-Test / Me-Test 유형 상수 ===
I_TEST_TYPES = ["Realist", "Romanticist", "Humanist", "Idealist", "Agent"]
ME_TEST_TYPES = ["Relation", "Trust", "Manual", "Self", "Culture"]

# === 유형 대응 관계 (GAP 분석용) ===
GAP_AXIS_MAP = {
    "relation_recognition": ("Realist", "Relation"),
    "emotion_trust": ("Romanticist", "Trust"),
    "social_control": ("Humanist", "Manual"),
    "independence_self": ("Idealist", "Self"),
    "achievement_culture": ("Agent", "Culture"),
}


# === 요청 스키마 ===


class WpiResponses(BaseModel):
    """WPI 응답 스키마 (순위 배정)."""

    rank_1: list[int] = Field(
        ..., min_length=3, max_length=3, description="1순위 3개 문항 ID"
    )
    rank_2: list[int] = Field(
        ..., min_length=4, max_length=4, description="2순위 4개 문항 ID"
    )
    rank_3: list[int] = Field(
        ..., min_length=5, max_length=5, description="3순위 5개 문항 ID"
    )

    @field_validator("rank_1", "rank_2", "rank_3")
    @classmethod
    def validate_item_range(cls, v: list[int]) -> list[int]:
        if not all(1 <= n <= 30 for n in v):
            raise ValueError("문항 번호는 1~30 범위여야 합니다")
        return v

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> "WpiResponses":
        all_items = self.rank_1 + self.rank_2 + self.rank_3
        if len(set(all_items)) != len(all_items):
            raise ValueError("중복 선택은 허용되지 않습니다")
        return self


class WpiSubmitRequest(BaseModel):
    """WPI 검사 응답 제출 요청."""

    test_type: Literal["i_test", "me_test"]
    responses: WpiResponses


# === 응답 스키마 ===


class WpiQuestion(BaseModel):
    """개별 문항 (클라이언트에 반환, type/weight 미포함)."""

    id: int
    text: str


class WpiQuestionsResponse(BaseModel):
    """문항 목록 응답."""

    test_type: Literal["i_test", "me_test"]
    questions: list[WpiQuestion]
    instructions: dict[str, str | int] = Field(
        default={
            "rank_1_count": 3,
            "rank_2_count": 4,
            "rank_3_count": 5,
            "description": "가장 나와 맞는 문항부터 순서대로 선택해주세요.",
        }
    )


class WpiTestScores(BaseModel):
    """검사 점수 (절대 점수)."""

    scores: dict[str, float] = Field(..., description="유형별 절대 점수")
    dominant_type: str = Field(..., description="우세 유형")


class WpiSubmitResponse(BaseModel):
    """검사 응답 제출 결과."""

    test_type: Literal["i_test", "me_test"]
    scores: dict[str, float] = Field(..., description="유형별 절대 점수")
    dominant_type: str
    status: Literal["i_test_completed", "me_test_completed", "both_completed"]
    message: str


WpiAiReportStatus = Literal["idle", "queued", "processing", "completed", "failed"]


class WpiAiReportGenerateRequest(BaseModel):
    """WPI AI 리포트 생성 요청."""

    force_regenerate: bool = Field(
        False,
        description="이미 생성된 리포트가 있어도 강제로 다시 생성할지 여부",
    )


class WpiAiReportResponse(BaseModel):
    """WPI AI 리포트 조회 응답."""

    result_id: UUID
    status: WpiAiReportStatus
    report_md: str | None = None
    error: str | None = None
    job_id: str | None = None
    updated_at: datetime | None = None


class WpiAiReportEnqueueResponse(WpiAiReportResponse):
    """WPI AI 리포트 생성 큐 등록 응답."""

    queued: bool
    message: str


class WpiAxisGap(BaseModel):
    """개별 축의 갭 정보."""

    i_type: str
    me_type: str
    i_score: float
    me_score: float
    gap: float  # i_score - me_score


class WpiGapAnalysis(BaseModel):
    """GAP 분석 결과."""

    axis_gaps: dict[str, WpiAxisGap]


class WpiProfileResponse(BaseModel):
    """WPI 프로필 전체 조회 응답."""

    id: UUID
    user_id: UUID
    i_test_scores: dict[str, float] | None = None
    me_test_scores: dict[str, float] | None = None
    gap_analysis: dict[str, Any] | None = None
    i_test_completed: bool
    me_test_completed: bool
    created_at: datetime
    updated_at: datetime | None = None

    # 계산된 필드 (응답 시 추가)
    dominant_i_type: str | None = None
    dominant_me_type: str | None = None

    model_config = {"from_attributes": True}


class WpiProfileStatus(BaseModel):
    """프로필 상태 간략 조회."""

    has_incomplete: bool = Field(False, description="미완료 검사 존재 여부")
    in_progress_id: str | None = Field(
        None, description="진행 중인 검사 ID (이어하기용)"
    )
    created_at: datetime | None = Field(None, description="진행 중인 검사 생성 시간")
    i_test_completed: bool = False
    me_test_completed: bool = False
    has_profile: bool = False


# === 범용 이력 스키마 ===


class ScanHistoryItem(BaseModel):
    """검사 이력 목록 아이템 (범용)."""

    id: UUID
    scan_type: str = Field(..., description="검사 유형 (wpi, wsi, mcdc 등)")
    completed: bool
    created_at: datetime
    summary: dict[str, Any] | None = Field(None, description="검사 요약 정보")

    model_config = {"from_attributes": True}


class ScanHistoryListResponse(BaseModel):
    """검사 이력 목록 응답 (범용)."""

    items: list[ScanHistoryItem]
    total: int
    limit: int
    offset: int


class ScanDetailResponse(BaseModel):
    """검사 상세 조회 응답 (범용).

    scan_type에 따라 data 구조가 달라짐.
    """

    id: UUID
    user_id: UUID
    scan_type: str
    completed: bool
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(..., description="검사 유형별 상세 데이터")
