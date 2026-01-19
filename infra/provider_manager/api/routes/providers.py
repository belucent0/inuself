"""Provider routes for Provider Manager API.

/providers 엔드포인트 라우터.
ProviderService를 통해 비즈니스 로직을 실행합니다.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

from services.provider_service import ProviderService
from models.schemas import (
    ProviderResponse,
    ProviderListResponse,
    OperationResponse,
    AllProcessesResponse,
    ProviderProcessResponse,
    KillZombiesResponse,
)

router = APIRouter(prefix="/providers", tags=["Providers"])

# Dependency: ProviderService 싱글톤
_service: Optional[ProviderService] = None


def get_service() -> ProviderService:
    """Get ProviderService singleton."""
    if _service is None:
        raise HTTPException(status_code=503, detail="ProviderService not initialized")
    return _service


def set_service(service: ProviderService):
    """Set ProviderService instance (for dependency injection)."""
    global _service
    _service = service


# 하위 호환성을 위한 별칭
def get_manager():
    """Deprecated: use get_service instead."""
    return get_service().manager if _service else None


def set_manager(manager):
    """Deprecated: use set_service instead."""
    pass  # main.py에서 set_service로 전환 필요


@router.get("", response_model=ProviderListResponse)
async def list_providers(service: ProviderService = Depends(get_service)):
    """모든 프로바이더 목록 조회."""
    result = await service.list_providers()
    return ProviderListResponse(
        providers=[
            ProviderResponse(**p) for p in result["providers"]
        ],
        total=result["total"],
    )


@router.get("/{name}", response_model=ProviderResponse)
async def get_provider(name: str, service: ProviderService = Depends(get_service)):
    """특정 프로바이더 정보 조회."""
    result = await service.get_provider(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ProviderResponse(**result)


@router.post("/{name}/load", response_model=OperationResponse)
async def load_provider(name: str, service: ProviderService = Depends(get_service)):
    """프로바이더 로드 (시작)."""
    try:
        result = await service.load_provider(name)
        return OperationResponse(status="success", message=f"Provider '{name}' loaded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/unload", response_model=OperationResponse)
async def unload_provider(name: str, service: ProviderService = Depends(get_service)):
    """프로바이더 언로드 (종료)."""
    try:
        result = await service.unload_provider(name)
        return OperationResponse(status="success", message=f"Provider '{name}' unloaded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/reload", response_model=OperationResponse)
async def reload_provider(name: str, service: ProviderService = Depends(get_service)):
    """프로바이더 리로드 (재시작)."""
    try:
        result = await service.reload_provider(name)
        return OperationResponse(status="success", message=f"Provider '{name}' reloaded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/enable", response_model=OperationResponse)
async def enable_provider(name: str, service: ProviderService = Depends(get_service)):
    """프로바이더 활성화."""
    try:
        result = await service.enable_provider(name)
        return OperationResponse(status="success", message=f"Provider '{name}' enabled")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/disable", response_model=OperationResponse)
async def disable_provider(name: str, service: ProviderService = Depends(get_service)):
    """프로바이더 비활성화."""
    try:
        result = await service.disable_provider(name)
        return OperationResponse(status="success", message=f"Provider '{name}' disabled")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# Process Inspection Endpoints
# ==========================================

@router.get("/{name}/processes", response_model=ProviderProcessResponse)
async def get_provider_processes(name: str, service: ProviderService = Depends(get_service)):
    """특정 프로바이더의 프로세스 정보 조회.

    포트를 사용하는 모든 프로세스와 좀비 여부를 확인합니다.
    """
    result = service.get_provider_processes(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return ProviderProcessResponse(**result)


@router.post("/{name}/kill-zombies", response_model=KillZombiesResponse)
async def kill_provider_zombies(name: str, service: ProviderService = Depends(get_service)):
    """특정 프로바이더의 좀비 프로세스 정리.

    관리 중인 PID 외의 프로세스를 종료합니다.
    """
    result = await service.kill_provider_zombies(name)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return KillZombiesResponse(**result)
