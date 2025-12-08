from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from ..core.config import get_settings
from ..db.models import File, FileStatus
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from .task_queue_adapter import get_task_queue
from .celery_queue import is_celery_task_in_queue

logger = logging.getLogger(__name__)

# 하위 호환성
ContentStatus = FileStatus


async def _fetch_files_by_status(statuses: Iterable[FileStatus]) -> list[File]:
    """주어진 상태 목록에 속한 파일을 조회."""
    session = AsyncSessionLocal()
    try:
        stmt = select(File).where(File.status.in_(list(statuses)))
        result = await session.execute(stmt)
        files = result.scalars().all()
        return files
    finally:
        await session.close()


# 하위 호환성을 위한 별칭
_fetch_contents_by_status = _fetch_files_by_status


async def requeue_processing_contents() -> int:
    """
    PROCESSING 또는 QUEUED 상태에서 멈춘 파일을 다시 ASR 큐에 등록한다.
    
    QUEUED 상태는 서버 재시작 시 큐에 작업이 없어졌을 수 있으므로 재등록이 필요합니다.
    오디오 파일만 재큐잉 (문서 파일은 제외).

    Returns:
        재큐잉된 파일 개수
    """
    from ..db.models import ContentType
    
    files = await _fetch_files_by_status([FileStatus.PROCESSING, FileStatus.QUEUED])
    # 오디오 파일만 필터링
    audio_files = [f for f in files if f.content_type == ContentType.AUDIO]
    
    if not audio_files:
        logger.info("No stuck PROCESSING or QUEUED audio files found.")
        return 0

    settings = get_settings()
    requeued = 0

    for file_obj in audio_files:
        session = AsyncSessionLocal()
        try:
            repo = FileRepository(session)
            await repo.update_file_status(file_obj.id, FileStatus.QUEUED)
            await repo.add_log(
                file_id=file_obj.id,
                log={"event": "requeued", "reason": "stuck_processing"},
                message="Automatically requeued after restart",
            )
            await session.commit()

            # Task Queue Adapter 사용 (Celery)
            task_queue = get_task_queue()
            task_queue.enqueue_asr_job(
                file_id=file_obj.id,
                storage_key=file_obj.object_key,
                original_filename=file_obj.filename,
                model_size=settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=settings.max_workers,
            )
            requeued += 1
            logger.info("Requeued ASR job for file_id=%s", file_obj.id)
        except Exception as exc:
            logger.exception("Failed to requeue ASR job for file_id=%s", file_obj.id)
            await session.rollback()
        finally:
            await session.close()

    return requeued


async def requeue_summarizing_contents() -> int:
    """
    SUMMARIZING 상태에서 멈춘 파일을 다시 LLM 큐에 등록한다.
    
    SUMMARIZING 상태는 워커 재시작 시 큐에 작업이 없어졌을 수 있으므로 재등록이 필요합니다.
    SUMMARY_FAILED 상태는 재시도하지 않음 (실패한 작업은 수동으로 재시도해야 함).

    Returns:
        재큐잉된 파일 개수
    """
    # SUMMARIZING 상태만 재큐잉 (SUMMARY_FAILED는 제외)
    files = await _fetch_files_by_status([
        FileStatus.SUMMARIZING,
    ])
    if not files:
        logger.info("No stuck SUMMARIZING files found.")
        return 0

    requeued = 0

    for file_obj in files:
        # 이미 큐에 있는 작업은 재큐잉하지 않음 (중복 방지)
        if is_celery_task_in_queue(file_id=file_obj.id, task_name="process_llm_task"):
            logger.debug("LLM job already in queue for file_id=%s, skipping requeue", file_obj.id)
            continue
        
        session = AsyncSessionLocal()
        try:
            repo = FileRepository(session)
            
            # 재큐잉 로그 추가
            reason = "stuck_summarizing" if file_obj.status == FileStatus.SUMMARIZING else "retry_summary_failed"
            await repo.add_llm_log(
                file_id=file_obj.id,
                log={"event": "requeued", "reason": reason, "previous_status": file_obj.status.value},
                message="Automatically requeued after restart",
            )
            
            # SUMMARY_FAILED 상태인 경우 SUMMARIZING으로 변경 (재시도)
            # SUMMARIZING 상태는 그대로 유지 (summarize() 함수가 다시 SUMMARIZING으로 설정함)
            if file_obj.status == FileStatus.SUMMARY_FAILED:
                await repo.update_file_status(file_obj.id, FileStatus.SUMMARIZING)
            
            await session.commit()

            # Task Queue Adapter 사용 (Celery)
            task_queue = get_task_queue()
            job_id = task_queue.enqueue_llm_job(file_id=file_obj.id)
            requeued += 1
            logger.info("Requeued LLM job for file_id=%s (previous_status=%s, job_id=%s)", 
                      file_obj.id, file_obj.status.value, job_id)
        except Exception as exc:
            logger.exception("Failed to requeue LLM job for file_id=%s", file_obj.id)
            await session.rollback()
        finally:
            await session.close()

    return requeued


