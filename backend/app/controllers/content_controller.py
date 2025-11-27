from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..schemas.content import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    ContentDetail,
    ContentListItem,
    UploadResponse,
)
from ..services.content_service import ContentService

router = APIRouter(prefix="/contents", tags=["contents"])


async def get_service(session: AsyncSession = Depends(get_session)) -> ContentService:
    return ContentService(session)


@router.get("", response_model=list[ContentListItem])
async def list_contents(limit: int = 20, offset: int = 0, service: ContentService = Depends(get_service)):
    return await service.list_contents(limit=limit, offset=offset)


@router.get("/{content_id}", response_model=ContentDetail)
async def get_content(content_id: int, service: ContentService = Depends(get_service)):
    try:
        return await service.get_content(content_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/upload", response_model=UploadResponse)
async def upload_content(file: UploadFile, service: ContentService = Depends(get_service)):
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("[Upload] 파일 업로드 요청 받음: filename=%s, content_type=%s", file.filename, file.content_type)
    print(f"[Upload] 파일 업로드 요청: {file.filename} ({file.content_type})")
    
    try:
        result = await service.upload_and_enqueue(file)
        logger.info("[Upload] 파일 업로드 성공: content_id=%s, filename=%s", result.content_id, file.filename)
        print(f"[Upload] OK 파일 업로드 완료: content_id={result.content_id}, filename={file.filename}")
        return result
    except Exception as exc:
        logger.exception("[Upload] 파일 업로드 실패: filename=%s, error=%s", file.filename, exc)
        print(f"[Upload] ERROR 파일 업로드 실패: {file.filename}, error={exc}")
        raise HTTPException(status_code=500, detail=f"업로드 실패: {str(exc)}") from exc


@router.delete("/queued", tags=["contents"])
async def delete_queued_contents(service: ContentService = Depends(get_service)):
    """QUEUED 상태인 모든 콘텐츠 삭제."""
    try:
        count = await service.delete_queued_contents()
        return {"deleted_count": count, "message": f"{count}개의 대기 중인 콘텐츠가 삭제되었습니다."}
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Delete queued contents failed")
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(exc)}") from exc


@router.post("/bulk-delete", response_model=BulkDeleteResponse, tags=["contents"])
async def bulk_delete_contents(
    payload: BulkDeleteRequest, service: ContentService = Depends(get_service)
):
    """체크박스로 선택된 콘텐츠를 상태에 관계없이 삭제."""
    try:
        deleted_ids, skipped_ids = await service.delete_contents_by_ids(payload.content_ids)
        message = "선택한 콘텐츠를 삭제했습니다."
        if not deleted_ids:
            message = "삭제 가능한 콘텐츠가 없습니다."
        elif skipped_ids:
            message = "일부 콘텐츠만 삭제되었습니다. (존재하지 않거나 이미 삭제된 항목 제외)"

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


