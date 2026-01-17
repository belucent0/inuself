"""Group routes for Provider Manager API.

/groups 엔드포인트 라우터.
ProviderService를 통해 비즈니스 로직을 실행합니다.
"""
from fastapi import APIRouter, HTTPException, Depends

from services.provider_service import ProviderService
from models.schemas import (
    GroupResponse,
    GroupListResponse,
    OperationResponse,
)
from .providers import get_service

router = APIRouter(prefix="/groups", tags=["Groups"])


@router.get("", response_model=GroupListResponse)
async def list_groups(service: ProviderService = Depends(get_service)):
    """모든 그룹 목록 조회."""
    result = await service.list_groups()
    return GroupListResponse(
        groups=[GroupResponse(**g) for g in result["groups"]],
        total=result["total"],
    )


@router.get("/{name}", response_model=GroupResponse)
async def get_group(name: str, service: ProviderService = Depends(get_service)):
    """특정 그룹 정보 조회."""
    result = await service.get_group(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")
    return GroupResponse(**result)


@router.post("/{name}/start", response_model=OperationResponse)
async def start_group(name: str, service: ProviderService = Depends(get_service)):
    """그룹 시작."""
    manager = service.manager
    if name not in manager.groups:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    try:
        await manager.start_groups([name])
        return OperationResponse(status="success", message=f"Group '{name}' started")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/stop", response_model=OperationResponse)
async def stop_group(name: str, service: ProviderService = Depends(get_service)):
    """그룹 종료."""
    manager = service.manager
    if name not in manager.groups:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    try:
        await manager.stop_groups([name])
        return OperationResponse(status="success", message=f"Group '{name}' stopped")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/restart", response_model=OperationResponse)
async def restart_group(name: str, service: ProviderService = Depends(get_service)):
    """그룹 재시작."""
    manager = service.manager
    if name not in manager.groups:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    try:
        await manager.restart_groups([name])
        return OperationResponse(status="success", message=f"Group '{name}' restarted")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/enable", response_model=OperationResponse)
async def enable_group(name: str, service: ProviderService = Depends(get_service)):
    """그룹 활성화."""
    success = service.manager.enable_group(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")
    return OperationResponse(status="success", message=f"Group '{name}' enabled")


@router.post("/{name}/disable", response_model=OperationResponse)
async def disable_group(name: str, service: ProviderService = Depends(get_service)):
    """그룹 비활성화."""
    success = service.manager.disable_group(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")
    return OperationResponse(status="success", message=f"Group '{name}' disabled")
