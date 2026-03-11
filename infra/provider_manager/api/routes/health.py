"""Health and Status routes for Provider Manager API.

/health, /status 엔드포인트 라우터.
ProviderService를 통해 비즈니스 로직을 실행합니다.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from services.provider_service import ProviderService
from services.redis_manager import get_redis_manager
from models.schemas import (
    StatusResponse,
    OperationResponse,
    HealthResponse,
    ServiceHealthItem,
    GroupStatusItem,
    ProviderStatusItem,
    AllProcessesResponse,
    KillZombiesResponse,
)
from .providers import get_service

router = APIRouter(tags=["Status"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """API 서버 헬스체크 — Redis 실제 연결 상태 반영."""
    redis_mgr = get_redis_manager()
    redis_status = "healthy" if redis_mgr and redis_mgr.is_connected else "connecting"
    overall = "healthy" if redis_status == "healthy" else "degraded"
    return HealthResponse(
        status=overall,
        services={"redis": ServiceHealthItem(status=redis_status)},
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(service: ProviderService = Depends(get_service)):
    """전체 상태 조회."""
    result = await service.get_status()

    groups = [
        GroupStatusItem(
            name=g["name"],
            order=g["order"],
            enabled=g["enabled"],
            providers=[
                ProviderStatusItem(**p) for p in g["providers"]
            ],
        )
        for g in result["groups"]
    ]

    return StatusResponse(
        groups=groups,
        running_providers=result["running_providers"],
        total_groups=result["total_groups"],
        total_providers=result["total_providers"],
        running_count=result["running_count"],
    )


@router.post("/start-all", response_model=OperationResponse)
async def start_all(service: ProviderService = Depends(get_service)):
    """모든 프로바이더 시작."""
    try:
        result = await service.start_all()
        return OperationResponse(status="success", message="All providers started")
    except Exception as e:
        return OperationResponse(status="failed", message=str(e))


@router.post("/stop-all", response_model=OperationResponse)
async def stop_all(service: ProviderService = Depends(get_service)):
    """모든 프로바이더 종료."""
    try:
        result = await service.stop_all()
        return OperationResponse(status="success", message="All providers stopped")
    except Exception as e:
        return OperationResponse(status="failed", message=str(e))


@router.post("/restart-all", response_model=OperationResponse)
async def restart_all(service: ProviderService = Depends(get_service)):
    """모든 프로바이더 재시작."""
    try:
        result = await service.restart_all()
        return OperationResponse(status="success", message="All providers restarted")
    except Exception as e:
        return OperationResponse(status="failed", message=str(e))


# ==========================================
# Process Inspection Endpoints
# ==========================================

@router.get("/processes", response_model=AllProcessesResponse)
async def get_all_processes(service: ProviderService = Depends(get_service)):
    """모든 프로바이더의 프로세스 정보 조회.

    각 프로바이더 포트를 사용하는 프로세스와 좀비 여부를 확인합니다.
    """
    result = service.get_all_processes()
    return AllProcessesResponse(**result)


@router.post("/kill-zombies", response_model=KillZombiesResponse)
async def kill_all_zombies(service: ProviderService = Depends(get_service)):
    """모든 프로바이더의 좀비 프로세스 정리.

    관리 중인 PID 외의 프로세스를 모두 종료합니다.
    """
    result = await service.kill_all_zombies()
    return KillZombiesResponse(**result)


# ==========================================
# Prometheus Metrics Endpoint
# ==========================================

@router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics(service: ProviderService = Depends(get_service)):
    """Prometheus 형식의 메트릭 반환.

    프로바이더 상태, 복구 시도 횟수, 활성 작업 수 등을 포함합니다.
    """
    return service.get_prometheus_metrics()
