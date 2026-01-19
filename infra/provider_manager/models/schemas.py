"""Pydantic schemas for Provider Manager API.

API 요청/응답을 위한 Pydantic 모델 정의.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ==========================================
# Provider Schemas
# ==========================================

class ProviderResponse(BaseModel):
    """단일 프로바이더 정보."""
    name: str = Field(..., description="프로바이더 이름")
    port: int = Field(..., description="포트 번호")
    health: str = Field(..., description="헬스체크 엔드포인트")
    enabled: bool = Field(..., description="활성화 여부")
    running: bool = Field(..., description="실행 중 여부")
    group: str = Field(..., description="소속 그룹명")


class ProviderListResponse(BaseModel):
    """프로바이더 목록 응답."""
    providers: List[ProviderResponse] = Field(..., description="프로바이더 목록")
    total: int = Field(..., description="전체 개수")


# ==========================================
# Group Schemas
# ==========================================

class GroupResponse(BaseModel):
    """단일 그룹 정보."""
    name: str = Field(..., description="그룹 이름")
    order: int = Field(..., description="시작 순서")
    enabled: bool = Field(..., description="활성화 여부")
    provider_count: int = Field(..., description="프로바이더 수")
    running_count: int = Field(..., description="실행 중 프로바이더 수")
    providers: List[str] = Field(..., description="프로바이더 이름 목록")


class GroupListResponse(BaseModel):
    """그룹 목록 응답."""
    groups: List[GroupResponse] = Field(..., description="그룹 목록")
    total: int = Field(..., description="전체 개수")


# ==========================================
# Status Schemas
# ==========================================

class ProviderStatusItem(BaseModel):
    """상태 응답의 프로바이더 항목."""
    name: str
    port: int
    enabled: bool
    running: bool


class GroupStatusItem(BaseModel):
    """상태 응답의 그룹 항목."""
    name: str
    order: int
    enabled: bool
    providers: List[ProviderStatusItem]


class StatusResponse(BaseModel):
    """전체 상태 응답."""
    groups: List[GroupStatusItem] = Field(..., description="그룹별 상태")
    running_providers: List[str] = Field(..., description="실행 중인 프로바이더 목록")
    total_groups: int = Field(..., description="전체 그룹 수")
    total_providers: int = Field(..., description="전체 프로바이더 수")
    running_count: int = Field(..., description="실행 중인 프로바이더 수")


# ==========================================
# Operation Schemas
# ==========================================

class OperationResponse(BaseModel):
    """작업 결과 응답."""
    status: str = Field(..., description="결과 상태 (success/failed)")
    message: str = Field(..., description="결과 메시지")


# ==========================================
# Health Schemas
# ==========================================

class ServiceHealthItem(BaseModel):
    """서비스 헬스 항목."""
    status: str = Field(..., description="상태 (healthy/unhealthy/unreachable)")
    code: Optional[int] = Field(None, description="HTTP 상태 코드")
    error: Optional[str] = Field(None, description="에러 메시지")


class HealthResponse(BaseModel):
    """헬스체크 응답."""
    status: str = Field(..., description="API 서버 상태")
    services: Optional[Dict[str, ServiceHealthItem]] = Field(None, description="서비스별 상태")


# ==========================================
# Job Schemas
# ==========================================

class JobItem(BaseModel):
    """작업 정보."""
    job_id: str = Field(..., description="작업 ID")
    request_id: str = Field(..., description="요청 ID")
    task_type: str = Field(..., description="작업 유형 (diarization, transcription, llm_completion, ocr)")
    provider: str = Field(..., description="처리 프로바이더")
    status: str = Field(..., description="상태 (pending, processing, completed, failed, timeout)")
    started_at: float = Field(..., description="시작 시간 (Unix timestamp)")
    duration: Optional[float] = Field(None, description="처리 시간 (초)")
    completed_at: Optional[float] = Field(None, description="완료 시간 (Unix timestamp)")
    error: Optional[str] = Field(None, description="에러 메시지")
    trace_id: Optional[str] = Field(None, description="클라이언트 TraceId (분산 추적용)")


class JobListResponse(BaseModel):
    """작업 목록 응답."""
    jobs: List[JobItem] = Field(..., description="작업 목록")
    total_active: int = Field(..., description="활성 작업 수")
    by_provider: Optional[Dict[str, int]] = Field(None, description="프로바이더별 작업 수")
    by_type: Optional[Dict[str, int]] = Field(None, description="유형별 작업 수")


class ProviderJobsResponse(BaseModel):
    """프로바이더별 작업 응답."""
    provider: str = Field(..., description="프로바이더 이름")
    active_jobs: int = Field(..., description="활성 작업 수")
    jobs: List[JobItem] = Field(..., description="작업 목록")


# ==========================================
# Process Inspection Schemas
# ==========================================

class ProcessInfo(BaseModel):
    """단일 프로세스 정보."""
    pid: str = Field(..., description="프로세스 ID")
    port: int = Field(..., description="포트 번호")
    local_address: str = Field(..., description="로컬 주소")
    state: str = Field(..., description="상태 (LISTENING, ESTABLISHED, etc.)")


class ProviderProcessInfo(BaseModel):
    """프로바이더의 프로세스 정보."""
    port: int = Field(..., description="프로바이더 포트")
    expected_pid: Optional[str] = Field(None, description="관리 중인 예상 PID")
    processes: List[ProcessInfo] = Field(..., description="실제 프로세스 목록")
    process_count: int = Field(..., description="프로세스 수")
    has_zombie: bool = Field(..., description="좀비 프로세스 존재 여부")


class ProviderProcessResponse(BaseModel):
    """특정 프로바이더 프로세스 조회 응답."""
    name: str = Field(..., description="프로바이더 이름")
    port: int = Field(..., description="프로바이더 포트")
    expected_pid: Optional[str] = Field(None, description="관리 중인 예상 PID")
    processes: List[ProcessInfo] = Field(..., description="실제 프로세스 목록")
    process_count: int = Field(..., description="프로세스 수")
    has_zombie: bool = Field(..., description="좀비 프로세스 존재 여부")


class AllProcessesResponse(BaseModel):
    """모든 프로바이더 프로세스 조회 응답."""
    providers: Dict[str, ProviderProcessInfo] = Field(..., description="프로바이더별 프로세스 정보")
    total_providers: int = Field(..., description="전체 프로바이더 수")
    total_processes: int = Field(..., description="전체 프로세스 수")
    zombie_count: int = Field(..., description="좀비 프로세스 있는 프로바이더 수")


class KillZombiesResponse(BaseModel):
    """좀비 프로세스 정리 응답."""
    provider: Optional[str] = Field(None, description="프로바이더 이름 (단일 정리 시)")
    port: Optional[int] = Field(None, description="프로바이더 포트 (단일 정리 시)")
    killed_count: Optional[int] = Field(None, description="종료된 프로세스 수 (단일 정리 시)")
    total_killed: Optional[int] = Field(None, description="전체 종료 프로세스 수 (전체 정리 시)")
    by_provider: Optional[Dict[str, int]] = Field(None, description="프로바이더별 종료 수 (전체 정리 시)")
