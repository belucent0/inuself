"""Jobs routes for Provider Manager API.

/jobs 엔드포인트 라우터 - 작업 조회 및 추적.
ProviderService를 통해 비즈니스 로직을 실행합니다.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException

from services.provider_service import ProviderService
from models.schemas import (
    JobItem,
    JobListResponse,
    ProviderJobsResponse,
)
from .providers import get_service

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("", response_model=JobListResponse)
async def list_jobs(
    provider: Optional[str] = Query(None, description="프로바이더 이름으로 필터링"),
    trace_id: Optional[str] = Query(None, description="TraceId로 필터링"),
    service: ProviderService = Depends(get_service),
):
    """활성 작업 목록 조회.

    - provider: 특정 프로바이더의 작업만 조회
    - trace_id: 특정 TraceId의 작업만 조회
    """
    result = await service.get_jobs(provider=provider, trace_id=trace_id)

    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])

    # trace_id로 조회한 경우
    if trace_id:
        return JobListResponse(
            jobs=[JobItem(**j) for j in result.get("jobs", [])],
            total_active=result.get("total", 0),
            by_provider=None,
            by_type=None,
        )

    # provider로 조회한 경우
    if provider:
        return JobListResponse(
            jobs=[JobItem(**j) for j in result.get("jobs", [])],
            total_active=result.get("active_jobs", 0),
            by_provider={provider: result.get("active_jobs", 0)},
            by_type=None,
        )

    # 전체 조회
    jobs = [
        JobItem(
            job_id=j["job_id"],
            request_id=j["request_id"],
            task_type=j["task_type"],
            provider=j["provider"],
            status=j["status"],
            started_at=j["started_at"],
            duration=j.get("duration"),
            completed_at=None,
            error=None,
            trace_id=None,
        )
        for j in result.get("jobs", [])
    ]

    return JobListResponse(
        jobs=jobs,
        total_active=result.get("total_active", 0),
        by_provider=result.get("by_provider"),
        by_type=result.get("by_type"),
    )


@router.get("/{job_id}", response_model=JobItem)
async def get_job(
    job_id: str,
    service: ProviderService = Depends(get_service),
):
    """개별 작업 조회."""
    result = await service.get_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return JobItem(**result)


@router.get("/trace/{trace_id}", response_model=JobListResponse)
async def get_jobs_by_trace(
    trace_id: str,
    service: ProviderService = Depends(get_service),
):
    """TraceId로 작업 목록 조회.

    분산 추적 시 특정 요청 체인의 모든 작업을 조회합니다.
    """
    result = await service.get_jobs(trace_id=trace_id)

    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])

    # processing 상태인 작업 수 계산
    jobs = result.get("jobs", [])
    active_count = sum(1 for j in jobs if j.get("status") == "processing")

    return JobListResponse(
        jobs=[JobItem(**j) for j in jobs],
        total_active=active_count,
        by_provider=None,
        by_type=None,
    )


@router.get("/provider/{provider}", response_model=ProviderJobsResponse)
async def get_provider_jobs(
    provider: str,
    service: ProviderService = Depends(get_service),
):
    """프로바이더별 작업 목록 조회."""
    result = await service.get_jobs(provider=provider)

    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])

    return ProviderJobsResponse(
        provider=provider,
        active_jobs=result.get("active_jobs", 0),
        jobs=[JobItem(**j) for j in result.get("jobs", [])],
    )
