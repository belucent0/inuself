from uuid import UUID
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    BackgroundTasks,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..core.auth import get_current_user_id, require_admin
from ..schemas.content import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    ContentDetail,
    ContentListItem,
    ContentListResponse,
    ReclusterSpeakersRequest,
    ReclusterSpeakersResponse,
    UploadResponse,
    YouTubeUploadRequest,
    YouTubeUploadResponse,
)
from ..services.youtube_service import (
    InvalidYouTubeURLError,
    VideoDurationExceededError,
    YouTubeService,
)
from ..schemas.file import FileUploadResponse
from ..services.content_service import ContentService
from ..services.file_service import FileService
from ..utils.event_publisher import publish_content_created

router = APIRouter(prefix="/contents", tags=["contents"])


async def get_service(session: AsyncSession = Depends(get_session)) -> ContentService:
    return ContentService(session)


async def get_file_service(session: AsyncSession = Depends(get_session)) -> FileService:
    return FileService(session)


@router.get("", response_model=ContentListResponse)
async def list_contents(
    page: int = Query(1, ge=1, description="페이지 번호 (1부터 시작)"),
    page_size: int = Query(10, ge=1, le=100, description="페이지당 항목 수 (최대 100)"),
    search: str | None = Query(None, min_length=1, max_length=100, description="파일명/제목 검색어"),
    user_id: UUID = Depends(get_current_user_id),
    file_service: FileService = Depends(get_file_service),
):
    return await file_service.list_files(user_id=user_id, page=page, page_size=page_size, search=search)


@router.get("/{content_id}", response_model=ContentDetail)
async def get_content(
    content_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    file_service: FileService = Depends(get_file_service),
):
    """content_id (UUID)로 콘텐츠 상세 조회."""
    try:
        return await file_service.get_file(content_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/upload", response_model=UploadResponse)
async def upload_content(
    request: Request,
    file: UploadFile,
    min_speakers: int | None = Query(None, ge=1, description="최소 화자 수 (선택사항)"),
    max_speakers: int | None = Query(None, ge=1, description="최대 화자 수 (선택사항)"),
    ocr_mode: str = Query(
        "document", description="OCR 처리 모드 ('document' 또는 'portray')"
    ),
    ocr_accuracy_mode: str = Query(
        "speed", description="OCR 처리 모드 ('speed' 또는 'accuracy')"
    ),
    accuracy_mode: str = Query(
        "speed", description="전사 모드 ('speed' 또는 'accuracy')"
    ),
    user_id: UUID = Depends(get_current_user_id),
    file_service: FileService = Depends(get_file_service),
):
    """파일 업로드 (오디오 및 문서 지원)."""
    from ..core.logging import logger
    from ..core.telemetry import get_trace_id

    # 디버그: Frontend에서 받은 traceparent 헤더 확인
    traceparent_header = request.headers.get("traceparent", "NOT_FOUND")
    current_trace_id = get_trace_id()
    logger.info(
        "[Upload] TRACE DEBUG: traceparent_header={}, current_trace_id={}",
        traceparent_header,
        current_trace_id,
    )

    logger.info(
        "[Upload] File upload request received: filename={}, content_type={}, min_speakers={}, max_speakers={}, ocr_mode={}, ocr_accuracy_mode={}, accuracy_mode={}",
        file.filename,
        file.content_type,
        min_speakers,
        max_speakers,
        ocr_mode,
        ocr_accuracy_mode,
        accuracy_mode,
    )
    print(
        f"[Upload] 파일 업로드 요청: {file.filename} ({file.content_type}), min_speakers={min_speakers}, max_speakers={max_speakers}, ocr_mode={ocr_mode}, ocr_accuracy_mode={ocr_accuracy_mode}, accuracy_mode={accuracy_mode}"
    )

    # Office 문서 체크
    if file.filename:
        filename_lower = file.filename.lower()
        office_extensions = (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
        if filename_lower.endswith(office_extensions):
            logger.info(
                "[Upload] Office document upload accepted (will use MarkItDown): filename={}", file.filename
            )

    try:
        result = await file_service.upload_and_enqueue(
            file,
            user_id=user_id,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            ocr_mode=ocr_mode,
            ocr_accuracy_mode=ocr_accuracy_mode,
            accuracy_mode=accuracy_mode,
        )
        upload_response = UploadResponse(
            content_id=result["content_id"], queued=True
        )
        logger.info(
            "[Upload] File upload successful: content_id={}, filename={}",
            result["content_id"],
            file.filename,
        )
        print(
            f"[Upload] OK 파일 업로드 완료: content_id={result['content_id']}, filename={file.filename}"
        )

        # 콘텐츠 생성 이벤트 발행 (클라이언트 목록 자동 새로고침용)
        publish_content_created(
            content_id=result["content_id"],
            filename=file.filename or "unknown",
            content_type=str(result.get("content_type", "AUDIO")),
            status="QUEUED",
        )

        return upload_response
    except Exception as exc:
        logger.exception(
            "[Upload] File upload failed: filename={}, error={}", file.filename, exc
        )
        print(f"[Upload] ERROR 파일 업로드 실패: {file.filename}, error={exc}")
        raise HTTPException(status_code=500, detail=f"업로드 실패: {str(exc)}") from exc


async def process_youtube_download_task(
    file_id: UUID, url: str, video_id: str, title: str, trace_id: str | None = None
):
    """백그라운드에서 실행되는 YouTube 다운로드 작업."""
    from ..db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        file_service = FileService(session)
        await file_service.perform_youtube_download(file_id, url, video_id, title, trace_id=trace_id)


@router.post("/upload-youtube", response_model=YouTubeUploadResponse)
async def upload_from_youtube(
    request: YouTubeUploadRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    user_id: UUID = Depends(get_current_user_id),
    file_service: FileService = Depends(get_file_service),
):
    """
    YouTube URL로부터 콘텐츠 생성 (비동기 다운로드).

    영상이 다운로드되고 자동으로 ASR 및 요약이 진행됩니다.
    1시간 이내의 영상만 지원됩니다.
    """
    from ..core.logging import logger

    trace_id = http_request.headers.get("x-trace-id")
    youtube_service = YouTubeService()

    # URL 유효성 검증
    try:
        video_id = youtube_service.validate_youtube_url(request.url)
    except InvalidYouTubeURLError:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    # 영상 정보 조회 (길이 확인) — 동기 yt-dlp 호출을 executor로 감싸서 이벤트 루프 비차단
    import asyncio
    loop = asyncio.get_running_loop()

    try:
        info = await loop.run_in_executor(None, youtube_service.get_video_info, request.url)
        duration = info.get("duration", 0)

        if duration > 7200:  # 2시간 = 7200초
            raise HTTPException(
                status_code=400, detail="2시간을 초과하는 영상은 처리할 수 없습니다"
            )

        title = info.get("title", f"youtube_{video_id}")

        logger.info(
            "[YouTube Upload] Request received: url=%s, title=%s, duration=%ss",
            request.url,
            title,
            duration,
        )

    except VideoDurationExceededError:
        raise HTTPException(
            status_code=400, detail="2시간을 초과하는 영상은 처리할 수 없습니다"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[YouTube Upload] Failed to get video info: %s", e)
        raise HTTPException(
            status_code=400, detail=f"Failed to get video info: {str(e)}"
        )

    # YouTube 다운로드 작업 준비 (DB 레코드 생성) + 백그라운드 태스크 등록
    try:
        # 1. 플레이스홀더 파일 생성 (PROCESSING 상태)
        file_obj = await file_service.prepare_youtube_placeholder(
            title=title, video_id=video_id, source_url=request.url, user_id=user_id
        )

        # 2. 백그라운드 태스크 등록
        background_tasks.add_task(
            process_youtube_download_task,
            file_id=file_obj.id,
            url=request.url,
            video_id=video_id,
            title=title,
            trace_id=trace_id,
        )

        logger.info(
            "[YouTube Upload] Job scheduled in background: file_id=%s, title=%s",
            file_obj.id,
            title,
        )

        # 콘텐츠 생성 이벤트 발행 (클라이언트 목록 자동 새로고침용)
        publish_content_created(
            content_id=file_obj.id,
            filename=f"{title}.mp4",
            content_type="AUDIO",
            status="PROCESSING",  # 처리중으로 표시
        )

        return YouTubeUploadResponse(
            content_id=file_obj.id,
            queued=True,
            message="YouTube 다운로드가 시작되었습니다",
        )

    except Exception as exc:
        logger.exception("[YouTube Upload] Failed to enqueue job: %s", exc)
        raise HTTPException(status_code=500, detail=f"업로드 실패: {str(exc)}") from exc


@router.delete("/queued", tags=["contents"])
async def delete_queued_contents(
    _admin=Depends(require_admin),
    service: ContentService = Depends(get_service),
):
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
    payload: BulkDeleteRequest,
    user_id: UUID = Depends(get_current_user_id),
    file_service: FileService = Depends(get_file_service),
):
    """체크박스로 선택된 콘텐츠를 상태에 관계없이 삭제."""
    try:
        deleted_ids, skipped_ids = await file_service.delete_files_by_ids(
            payload.content_ids, user_id=user_id
        )
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
    content_id: UUID,
    type: str = Query(
        ...,
        description="재처리 타입: 'download', 'asr', 'summary', 또는 'ocr'",
    ),
    min_speakers: int | None = Query(
        None, ge=1, description="최소 화자 수 (ASR 재처리 시에만 사용)"
    ),
    max_speakers: int | None = Query(
        None, ge=1, description="최대 화자 수 (ASR 재처리 시에만 사용)"
    ),
    ocr_mode: str = Query(
        "document", description="OCR 처리 모드 ('document' 또는 'portray')"
    ),
    ocr_accuracy_mode: str = Query(
        "speed", description="OCR 정확도 모드 ('speed' 또는 'accuracy')"
    ),
    accuracy_mode: str = Query(
        "speed", description="전사 모드 ('speed' 또는 'accuracy')"
    ),
    user_id: UUID = Depends(get_current_user_id),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    service: ContentService = Depends(get_service),
    file_service: FileService = Depends(get_file_service),
):
    """
    실패한 콘텐츠를 재처리합니다.

    Query Parameters:
        type: "download" (다운로드 재시도), "asr" (ASR 재처리), "summary" (LLM 요약 재처리), 또는 "ocr" (OCR 재처리)
        min_speakers: 최소 화자 수 (선택사항, ASR 재처리 시에만 사용)
        max_speakers: 최대 화자 수 (선택사항, ASR 재처리 시에만 사용)
        ocr_mode: OCR 처리 모드 (선택사항, OCR 재처리 시에만 사용: "document", "portray")
        accuracy_mode: 전사 모드 (선택사항, ASR 재처리 시에만 사용: "speed", "accuracy")
    """
    try:
        # 다운로드 재시도는 백그라운드 태스크가 필요하므로 컨트롤러에서 처리
        if type.lower() == "download":
            from ..db.models import FileStatus

            file_obj = await file_service.file_repo.get_file(content_id)
            if not file_obj:
                raise HTTPException(status_code=404, detail="콘텐츠를 찾을 수 없습니다")

            # user_id 검증
            if file_obj.content and file_obj.content.user_id != user_id:
                raise HTTPException(status_code=403, detail="해당 콘텐츠에 대한 권한이 없습니다")

            if not file_obj.source_url:
                raise HTTPException(status_code=400, detail="다운로드 재시도 불가: 원본 URL 없음")

            await file_service.file_repo.update_file_status(
                content_id, FileStatus.PULLING, triggered_by="manual_retry", validate=False
            )
            await file_service.file_repo.add_log(
                file_id=content_id,
                log={"event": "manual_retry", "type": "download"},
                message="Manual download retry requested by user",
            )
            await file_service.session.commit()

            youtube_service = YouTubeService()
            video_id = youtube_service.validate_youtube_url(file_obj.source_url)

            background_tasks.add_task(
                process_youtube_download_task,
                file_id=file_obj.id,
                url=file_obj.source_url,
                video_id=video_id,
                title=file_obj.filename.replace(".mp4", ""),
            )

            return {"success": True, "message": "다운로드 재시도가 시작되었습니다"}

        result = await service.retry_processing(
            content_id,
            type,
            user_id=user_id,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            ocr_mode=ocr_mode,
            ocr_accuracy_mode=ocr_accuracy_mode,
            accuracy_mode=accuracy_mode,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception("Retry processing failed")
        raise HTTPException(status_code=500, detail=f"재처리 실패: {str(exc)}") from exc


@router.post("/{content_id}/translate", tags=["contents"])
async def translate_content(
    content_id: UUID,
    target_lang: str = Query("ko", description="번역 대상 언어 (현재 'ko'만 지원)"),
    user_id: UUID = Depends(get_current_user_id),
    service: ContentService = Depends(get_service),
):
    """Transcript 청크 단위 한국어 번역 (PR-Translate.1 수동 트리거).

    AUDIO + COMPLETED 콘텐츠에만 허용. 5세그먼트씩 청크 단위로 LLM 호출,
    이미 번역된 segment는 skip. PR-B SSE 인프라로 chunk 완료 이벤트 발행 →
    frontend 점진 표시.
    """
    try:
        return await service.translate_transcription_content(
            content_id, target_lang=target_lang, user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception("Translation failed")
        raise HTTPException(status_code=500, detail=f"번역 실패: {str(exc)}") from exc


@router.post(
    "/{content_id}/summary/blocks/{block_key}/regenerate",
    tags=["contents"],
)
async def regenerate_summary_block(
    content_id: UUID,
    block_key: str,
    user_id: UUID = Depends(get_current_user_id),
    service: ContentService = Depends(get_service),
):
    """단일 summary block을 재생성한다 (PR-C 부분 재생성).

    block_key가 group_extracts에 속하면(title/keywords/headings) 그룹 전체가
    함께 재생성된다. dynamic block(section_*)은 해당 인덱스만 재생성.
    """
    try:
        return await service.regenerate_summary_block(
            content_id, block_key, user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception("Block regenerate failed")
        raise HTTPException(
            status_code=500, detail=f"Block 재생성 실패: {str(exc)}"
        ) from exc


@router.post(
    "/{content_id}/recluster-speakers",
    response_model=ReclusterSpeakersResponse,
    tags=["contents"],
)
async def recluster_speakers(
    content_id: UUID,
    request: ReclusterSpeakersRequest,
    user_id: UUID = Depends(get_current_user_id),
    service: ContentService = Depends(get_service),
):
    """
    저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다.

    이 API는 GPU 연산 없이 CPU만으로 빠르게 화자 분리를 재조정합니다.
    segment_embeddings가 저장된 콘텐츠에만 사용 가능합니다.
    """
    try:
        result = await service.recluster_speakers(
            file_id=content_id,  # UUID
            user_id=user_id,
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
        raise HTTPException(
            status_code=500, detail=f"재클러스터링 실패: {str(exc)}"
        ) from exc
