from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..schemas.content import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    ContentDetail,
    ContentListItem,
    ContentListResponse,
    ReclusterSpeakersRequest,
    ReclusterSpeakersResponse,
    UploadResponse,
)
from ..schemas.file import FileUploadResponse
from ..services.content_service import ContentService
from ..services.file_service import FileService

router = APIRouter(prefix="/contents", tags=["contents"])


async def get_service(session: AsyncSession = Depends(get_session)) -> ContentService:
    return ContentService(session)


async def get_file_service(session: AsyncSession = Depends(get_session)) -> FileService:
    return FileService(session)


@router.get("", response_model=ContentListResponse)
async def list_contents(
    page: int = Query(1, ge=1, description="페이지 번호 (1부터 시작)"),
    page_size: int = Query(10, ge=1, le=100, description="페이지당 항목 수 (최대 100)"),
    file_service: FileService = Depends(get_file_service)
):
    return await file_service.list_files(page=page, page_size=page_size)


@router.get("/{content_id}", response_model=ContentDetail)
async def get_content(content_id: int, file_service: FileService = Depends(get_file_service)):
    try:
        return await file_service.get_file(content_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/upload", response_model=UploadResponse)
async def upload_content(
    file: UploadFile,
    min_speakers: int | None = Query(None, ge=1, description="최소 화자 수 (선택사항)"),
    max_speakers: int | None = Query(None, ge=1, description="최대 화자 수 (선택사항)"),
    ocr_mode: str = Query("basic", description="OCR 처리 모드 ('basic' 또는 'docling')"),
    file_service: FileService = Depends(get_file_service)
):
    """파일 업로드 (오디오 및 문서 지원)."""
    from ..core.logging import logger
    
    logger.info("[Upload] File upload request received: filename={}, content_type={}, min_speakers={}, max_speakers={}, ocr_mode={}", 
               file.filename, file.content_type, min_speakers, max_speakers, ocr_mode)
    print(f"[Upload] 파일 업로드 요청: {file.filename} ({file.content_type}), min_speakers={min_speakers}, max_speakers={max_speakers}, ocr_mode={ocr_mode}")
    
    try:
        result = await file_service.upload_and_enqueue(
            file, 
            min_speakers=min_speakers, 
            max_speakers=max_speakers, 
            ocr_mode=ocr_mode,
        )
        # 하위 호환성을 위해 content_id로 변환
        upload_response = UploadResponse(content_id=result["file_id"], queued=True)
        logger.info("[Upload] File upload successful: file_id={}, filename={}", result["file_id"], file.filename)
        print(f"[Upload] OK 파일 업로드 완료: file_id={result['file_id']}, filename={file.filename}")
        return upload_response
    except Exception as exc:
        logger.exception("[Upload] File upload failed: filename={}, error={}", file.filename, exc)
        print(f"[Upload] ERROR 파일 업로드 실패: {file.filename}, error={exc}")
        raise HTTPException(status_code=500, detail=f"업로드 실패: {str(exc)}") from exc


@router.delete("/queued", tags=["contents"])
async def delete_queued_contents(service: ContentService = Depends(get_service)):
    """QUEUED 상태인 모든 콘텐츠 삭제."""
    try:
        count = await service.delete_queued_contents()
        return {"deleted_count": count, "message": f"{count} queued contents deleted."}
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Delete queued contents failed")
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(exc)}") from exc


@router.post("/bulk-delete", response_model=BulkDeleteResponse, tags=["contents"])
async def bulk_delete_contents(
    payload: BulkDeleteRequest, file_service: FileService = Depends(get_file_service)
):
    """체크박스로 선택된 콘텐츠를 상태에 관계없이 삭제."""
    try:
        deleted_ids, skipped_ids = await file_service.delete_files_by_ids(payload.content_ids)
        message = "선택된 콘텐츠를 삭제했습니다."
        if not deleted_ids:
            message = "삭제 가능한 콘텐츠를 찾을 수 없습니다."
        elif skipped_ids:
            message = "일부 콘텐츠를 삭제했습니다. (존재하지 않거나 이미 삭제된 항목은 제외됨)"

        return BulkDeleteResponse(
            deleted_count=len(deleted_ids),
            deleted_ids=deleted_ids,
            skipped_ids=skipped_ids,
            message=message,
        )
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Bulk delete contents failed")
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(exc)}") from exc


@router.post("/{content_id}/retry", tags=["contents"])
async def retry_processing(
    content_id: int,
    type: str = Query(..., description="재처리 타입: 'asr' (ASR 재처리) 또는 'summary' (LLM 요약 재처리)"),
    min_speakers: int | None = Query(None, ge=1, description="최소 화자 수 (ASR 재처리 시에만 사용)"),
    max_speakers: int | None = Query(None, ge=1, description="최대 화자 수 (ASR 재처리 시에만 사용)"),
    service: ContentService = Depends(get_service)
):
    """
    실패한 콘텐츠를 재처리합니다.
    
    Query Parameters:
        type: "asr" (ASR 재처리), "summary" (LLM 요약 재처리), 또는 "ocr" (OCR 재처리)
        min_speakers: 최소 화자 수 (선택사항, ASR 재처리 시에만 사용)
        max_speakers: 최대 화자 수 (선택사항, ASR 재처리 시에만 사용)
    """
    try:
        result = await service.retry_processing(content_id, type, min_speakers=min_speakers, max_speakers=max_speakers)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Retry processing failed")
        raise HTTPException(status_code=500, detail=f"재처리 실패: {str(exc)}") from exc


@router.post("/{content_id}/recluster-speakers", response_model=ReclusterSpeakersResponse, tags=["contents"])
async def recluster_speakers(
    content_id: int,
    request: ReclusterSpeakersRequest,
    service: ContentService = Depends(get_service)
):
    """
    저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다.
    
    이 API는 GPU 연산 없이 CPU만으로 빠르게 화자 분리를 재조정합니다.
    segment_embeddings가 저장된 콘텐츠에만 사용 가능합니다.
    """
    try:
        result = await service.recluster_speakers(
            content_id=content_id,
            num_speakers=request.num_speakers,
            similarity_threshold=request.similarity_threshold,
        )
        return ReclusterSpeakersResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Reclustering failed")
        raise HTTPException(status_code=500, detail=f"재클러스터링 실패: {str(exc)}") from exc


