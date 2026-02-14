"""Scan API 컨트롤러.

심리검사(WPI, WSI, MCDC 등) 관련 API 엔드포인트.
범용 scan_result 테이블 사용.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..repositories.scan_repository import ScanRepository
from ..schemas.wpi import (
    ScanDetailResponse,
    ScanHistoryItem,
    ScanHistoryListResponse,
    WpiAiReportEnqueueResponse,
    WpiAiReportGenerateRequest,
    WpiAiReportResponse,
    WpiProfileStatus,
    WpiQuestionsResponse,
    WpiSubmitRequest,
    WpiSubmitResponse,
)
from ..services.wpi_service import WpiService

router = APIRouter(prefix="/scan", tags=["scan"])


# === 의존성 ===


async def get_wpi_service(session: AsyncSession = Depends(get_session)) -> WpiService:
    return WpiService(session)


async def get_scan_repository(
    session: AsyncSession = Depends(get_session),
) -> ScanRepository:
    return ScanRepository(session)


# TODO: 실제 인증 구현 시 교체
async def get_current_user_id() -> UUID:
    """현재 사용자 ID 반환 (임시 구현)."""
    return UUID("01234567-89ab-cdef-0123-456789abcdef")


# === 범용 이력 API ===


@router.get("/history", response_model=ScanHistoryListResponse)
async def get_scan_history(
    scan_type: str | None = Query(None, description="검사 유형 필터 (wpi, wsi 등)"),
    status: str | None = Query(None, description="상태 필터 (completed, in_progress)"),
    limit: int = Query(20, ge=1, le=100, description="최대 조회 개수"),
    offset: int = Query(0, ge=0, description="시작 위치"),
    repo: ScanRepository = Depends(get_scan_repository),
    wpi_service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """전체 검사 이력 목록 조회."""
    results = await repo.get_history(
        user_id, scan_type=scan_type, status=status, limit=limit, offset=offset
    )
    total = await repo.count(user_id, scan_type=scan_type, status=status)

    items = []
    for result in results:
        # 검사 유형별 summary 생성
        summary = None
        if result.scan_type == "wpi":
            # 점수를 동적으로 계산
            enriched_data = wpi_service.enrich_with_scores(result.data)
            i_test = enriched_data.get("i_test")
            me_test = enriched_data.get("me_test")

            dominant_i = i_test.get("dominant_type") if i_test else None
            dominant_me = me_test.get("dominant_type") if me_test else None

            summary = {
                "dominant_i_type": dominant_i,
                "dominant_me_type": dominant_me,
            }
        # TODO: WSI, MCDC 등 추가

        items.append(
            ScanHistoryItem(
                id=result.id,
                scan_type=result.scan_type,
                completed=result.status == "completed",
                created_at=result.created_at,
                summary=summary,
            )
        )

    return ScanHistoryListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/history/{result_id}", response_model=ScanDetailResponse)
async def get_scan_detail(
    result_id: UUID,
    repo: ScanRepository = Depends(get_scan_repository),
    wpi_service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """특정 검사 상세 조회."""
    result = await repo.get_by_id(result_id)

    if result is None:
        raise HTTPException(status_code=404, detail="검사 결과를 찾을 수 없습니다")

    if result.user_id != user_id:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    # WPI인 경우 점수를 동적으로 계산
    data = result.data
    if result.scan_type == "wpi":
        data = wpi_service.enrich_with_scores(result.data)

    return ScanDetailResponse(
        id=result.id,
        user_id=result.user_id,
        scan_type=result.scan_type,
        completed=result.status == "completed",
        created_at=result.created_at,
        updated_at=result.updated_at,
        data=data,
    )


@router.get("/history/{result_id}/ai-report", response_model=WpiAiReportResponse)
async def get_wpi_ai_report(
    result_id: UUID,
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """WPI AI 리포트 상태/결과 조회."""

    try:
        payload = await service.get_ai_report(user_id=user_id, result_id=result_id)
        return WpiAiReportResponse(**payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post(
    "/history/{result_id}/ai-report", response_model=WpiAiReportEnqueueResponse
)
async def enqueue_wpi_ai_report(
    result_id: UUID,
    request: WpiAiReportGenerateRequest | None = None,
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """WPI AI 리포트 생성 작업을 큐에 등록."""

    force_regenerate = request.force_regenerate if request else False

    try:
        payload = await service.enqueue_ai_report_generation(
            user_id=user_id,
            result_id=result_id,
            force_regenerate=force_regenerate,
        )
        return WpiAiReportEnqueueResponse(**payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# === WPI 엔드포인트 ===


@router.get("/wpi/questions", response_model=WpiQuestionsResponse)
async def get_wpi_questions(
    test_type: Literal["i_test", "me_test"] = Query(..., description="검사 종류"),
    shuffle: bool = Query(True, description="문항 순서 셔플 여부"),
    service: WpiService = Depends(get_wpi_service),
):
    """WPI 검사 문항 조회."""
    questions = service.get_questions(test_type, shuffle=shuffle)
    return WpiQuestionsResponse(test_type=test_type, questions=questions)


@router.post("/wpi/submit", response_model=WpiSubmitResponse)
async def submit_wpi_test(
    request: WpiSubmitRequest,
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """WPI 검사 응답 제출 및 채점."""
    try:
        if request.test_type == "i_test":
            result, scores, dominant = await service.submit_i_test(
                user_id, request.responses
            )
            return WpiSubmitResponse(
                test_type="i_test",
                scores=scores,
                dominant_type=dominant,
                status="i_test_completed",
                message="I-Test 완료! Me-Test를 진행해주세요.",
            )
        else:
            result, scores, dominant, gap_analysis = await service.submit_me_test(
                user_id, request.responses
            )
            return WpiSubmitResponse(
                test_type="me_test",
                scores=scores,
                dominant_type=dominant,
                status="both_completed",
                message="WPI 검사가 완료되었습니다! 결과를 확인하세요.",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/wpi/profile", response_model=ScanDetailResponse)
async def get_wpi_latest_profile(
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """현재 사용자의 최신 WPI 결과 조회."""
    result = await service.get_latest(user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="WPI 검사 결과가 없습니다")

    # 점수를 동적으로 계산
    enriched_data = service.enrich_with_scores(result.data)

    return ScanDetailResponse(
        id=result.id,
        user_id=result.user_id,
        scan_type=result.scan_type,
        completed=result.status == "completed",
        created_at=result.created_at,
        updated_at=result.updated_at,
        data=enriched_data,
    )


@router.get("/wpi/status", response_model=WpiProfileStatus)
async def get_wpi_status(
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """WPI 검사 진행 상태 조회."""
    status = await service.get_status(user_id)
    return WpiProfileStatus(**status)


@router.delete("/wpi/in-progress")
async def delete_wpi_in_progress(
    service: WpiService = Depends(get_wpi_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """진행 중인 WPI 검사 삭제 (새로 시작하기 위해)."""
    deleted = await service.delete_in_progress(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="진행 중인 검사가 없습니다")
    return {"message": "진행 중인 검사가 삭제되었습니다"}
