"""Langfuse 대시보드 API 컨트롤러.

모니터링 페이지에서 사용하는 Langfuse 집계/트레이스 조회용 읽기 전용 엔드포인트.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.langfuse_dashboard_service import LangfuseDashboardService

router = APIRouter(prefix="/admin/langfuse", tags=["langfuse"])
service = LangfuseDashboardService()


class LangfuseSummaryResponse(BaseModel):
    trace_count: int
    error_count: int
    avg_latency_ms: float
    total_cost_usd: float
    avg_score: float
    score_count: int


class LangfuseTrendPointResponse(BaseModel):
    bucket: str
    request_count: int
    error_count: int
    avg_latency_ms: float
    cost_usd: float


class LangfuseOverviewResponse(BaseModel):
    enabled: bool
    configured: bool
    host: str | None = None
    summary: LangfuseSummaryResponse
    trend: list[LangfuseTrendPointResponse]
    errors: list[str]


class LangfuseTraceResponse(BaseModel):
    trace_id: str
    name: str
    display_name: str
    status: str
    latency_ms: float
    cost_usd: float
    created_at: str | float | None = None
    project_id: str | None = None
    query_preview: str | None = None
    mode: str | None = None
    thread_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    message_id: str | None = None
    user_message_id: str | None = None
    turn_index: int | None = None
    trace_path: str | None = None
    session_path: str | None = None
    input_preview: str | None = None
    output_preview: str | None = None


class LangfuseObservationResponse(BaseModel):
    observation_id: str
    trace_id: str
    parent_observation_id: str | None = None
    name: str
    type: str | None = None
    level: str | None = None
    status: str
    status_message: str | None = None
    model: str | None = None
    start_time: str | float | None = None
    end_time: str | float | None = None
    latency_ms: float
    cost_usd: float
    input_preview: str | None = None
    output_preview: str | None = None


class LangfuseSessionTimelineDataResponse(BaseModel):
    session_id: str
    trace_count: int
    traces: list[LangfuseTraceResponse]


class LangfuseTraceDetailResponse(BaseModel):
    enabled: bool
    configured: bool
    trace: LangfuseTraceResponse | None = None
    observations: list[LangfuseObservationResponse]
    session: LangfuseSessionTimelineDataResponse | None = None
    errors: list[str]


class LangfuseTracesResponse(BaseModel):
    enabled: bool
    configured: bool
    traces: list[LangfuseTraceResponse]
    errors: list[str]


class LangfuseSessionTimelineResponse(BaseModel):
    enabled: bool
    configured: bool
    session_id: str
    traces: list[LangfuseTraceResponse]
    errors: list[str]


@router.get("/overview", response_model=LangfuseOverviewResponse)
async def get_langfuse_overview(
    hours: int = Query(24, ge=1, le=168, description="조회 기간 (시간 단위, 최대 7일)"),
    limit: int = Query(100, ge=10, le=100, description="조회할 trace/scores 최대 개수"),
):
    return await service.fetch_overview(hours=hours, limit=limit)


@router.get("/traces", response_model=LangfuseTracesResponse)
async def get_langfuse_recent_traces(
    limit: int = Query(20, ge=1, le=100, description="조회할 최근 trace 개수"),
):
    return await service.fetch_recent_traces(limit=limit)


@router.get("/traces/{trace_id}", response_model=LangfuseTraceDetailResponse)
async def get_langfuse_trace_detail(trace_id: str):
    return await service.fetch_trace_detail(trace_id=trace_id)


@router.get("/sessions/{session_id}", response_model=LangfuseSessionTimelineResponse)
async def get_langfuse_session_timeline(
    session_id: str,
    limit: int = Query(50, ge=1, le=100, description="조회할 session trace 최대 개수"),
):
    return await service.fetch_session_timeline(session_id=session_id, limit=limit)
