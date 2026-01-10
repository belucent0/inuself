"""YouTube 다운로드 워커 프로세서

YouTube URL에서 영상을 다운로드하고 ASR 파이프라인에 연결합니다.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import upload_fileobj
from ..core.system_utils import (
    cleanup_worker_event_loop,
    setup_worker_event_loop,
    WorkerSessionContext,
)
from ..db.models import FileStatus
from ..repositories.file_repository import FileRepository
from ..services.youtube_service import YouTubeDownloadError, YouTubeService
from .task_queue_adapter import get_task_queue

settings = get_settings()


def process_youtube_download_job(
    file_id: int,
    youtube_url: str,
    storage_key: str,
    original_filename: str,
) -> None:
    """
    YouTube 다운로드 → 스토리지 업로드 → ASR 큐 등록
    
    이 함수는 Celery 워커에서 호출됩니다.
    
    Args:
        file_id: 파일 ID (DB에 이미 생성되어 있음)
        youtube_url: YouTube 영상 URL
        storage_key: 스토리지에 저장할 키
        original_filename: 원본 파일명 (제목이 변경될 수 있음)
    """
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(
            _process_youtube_download(
                file_id=file_id,
                youtube_url=youtube_url,
                storage_key=storage_key,
                original_filename=original_filename,
            )
        )
    finally:
        cleanup_worker_event_loop(loop)


async def _process_youtube_download(
    file_id: int,
    youtube_url: str,
    storage_key: str,
    original_filename: str,
) -> None:
    """비동기 YouTube 다운로드 처리"""

    logger.info(f"[YouTube Worker] Starting download: file_id={file_id}, url={youtube_url}")

    youtube_service = YouTubeService()

    # 상태 업데이트: PROCESSING
    async with WorkerSessionContext() as session:
        file_repo = FileRepository(session)
        await file_repo.update_file_status(file_id, FileStatus.PROCESSING)
        await file_repo.add_log(
            file_id=file_id,
            log={"event": "youtube_download_start", "url": youtube_url},
            message="YouTube 다운로드 시작",
        )
        await session.commit()

    try:
        # 임시 디렉토리에 다운로드
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # YouTube 다운로드 (360p/240p) - 동기 함수를 executor에서 실행
            loop = asyncio.get_running_loop()
            video_info = await loop.run_in_executor(
                None,
                youtube_service.download_video,
                youtube_url,
                temp_path
            )

            logger.info(f"[YouTube Worker] Download complete: {video_info.title}")

            # 스토리지에 업로드
            def _upload_to_storage():
                with open(video_info.temp_path, 'rb') as f:
                    upload_fileobj(f, key=storage_key)

            await loop.run_in_executor(None, _upload_to_storage)

            logger.info(f"[YouTube Worker] Uploaded to storage: {storage_key}")

            # 파일명 업데이트 (실제 제목으로)
            new_filename = f"{video_info.title[:50]}.mp4"
            async with WorkerSessionContext() as session:
                file_repo = FileRepository(session)
                file_obj = await file_repo.get_file(file_id)
                if file_obj:
                    file_obj.filename = new_filename
                await file_repo.add_log(
                    file_id=file_id,
                    log={
                        "event": "youtube_download_complete",
                        "title": video_info.title,
                        "duration": video_info.duration,
                    },
                    message=f"YouTube 다운로드 완료: {video_info.title}",
                )
                await session.commit()

            # ASR 작업 큐잉
            task_queue = get_task_queue()
            job_id = task_queue.enqueue_asr_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=new_filename,
                model_size=settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=settings.max_workers,
                min_speakers=None,  # 자동 감지
                max_speakers=None,  # 자동 감지
            )

            logger.info(f"[YouTube Worker] ASR job enqueued: file_id={file_id}, job_id={job_id}")

    except YouTubeDownloadError as e:
        logger.error(f"[YouTube Worker] Download failed: {e}")
        async with WorkerSessionContext() as session:
            file_repo = FileRepository(session)
            await file_repo.update_file_status(file_id, FileStatus.ASR_FAILED)
            await file_repo.add_log(
                file_id=file_id,
                log={"event": "youtube_download_failed", "error": str(e)},
                message=f"YouTube 다운로드 실패: {e}",
            )
            await session.commit()
        raise

    except Exception as e:
        logger.exception(f"[YouTube Worker] Unexpected error: {e}")
        async with WorkerSessionContext() as session:
            file_repo = FileRepository(session)
            await file_repo.update_file_status(file_id, FileStatus.ASR_FAILED)
            await file_repo.add_log(
                file_id=file_id,
                log={"event": "youtube_error", "error": str(e)},
                message=f"YouTube 처리 오류: {e}",
            )
            await session.commit()
        raise
