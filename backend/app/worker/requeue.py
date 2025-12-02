from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from ..core.config import get_settings
from ..db.models import Content, ContentStatus
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository
from .task_queue_adapter import get_task_queue
from .celery_queue import is_celery_task_in_queue

logger = logging.getLogger(__name__)


async def _fetch_contents_by_status(statuses: Iterable[ContentStatus]) -> list[Content]:
    """주어진 상태 목록에 속한 콘텐츠를 조회."""
    session = AsyncSessionLocal()
    try:
        stmt = select(Content).where(Content.status.in_(list(statuses)))
        result = await session.execute(stmt)
        contents = result.scalars().all()
        return contents
    finally:
        await session.close()


async def requeue_processing_contents() -> int:
    """
    PROCESSING 또는 QUEUED 상태에서 멈춘 콘텐츠를 다시 ASR 큐에 등록한다.
    
    QUEUED 상태는 서버 재시작 시 큐에 작업이 없어졌을 수 있으므로 재등록이 필요합니다.

    Returns:
        재큐잉된 콘텐츠 개수
    """
    contents = await _fetch_contents_by_status([ContentStatus.PROCESSING, ContentStatus.QUEUED])
    if not contents:
        logger.info("No stuck PROCESSING or QUEUED contents found.")
        return 0

    settings = get_settings()
    requeued = 0

    for content in contents:
        session = AsyncSessionLocal()
        try:
            repo = ContentRepository(session)
            await repo.update_content_status(content.id, ContentStatus.QUEUED)
            await repo.add_log(
                content_id=content.id,
                log={"event": "requeued", "reason": "stuck_processing"},
                message="Automatically requeued after restart",
            )
            await session.commit()

            # Task Queue Adapter 사용 (Celery)
            task_queue = get_task_queue()
            task_queue.enqueue_asr_job(
                content_id=content.id,
                storage_key=content.object_key,
                original_filename=content.filename,
                model_size=settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=settings.max_workers,
            )
            requeued += 1
            logger.info("Requeued ASR job for content_id=%s", content.id)
        except Exception as exc:
            logger.exception("Failed to requeue ASR job for content_id=%s", content.id)
            await session.rollback()
        finally:
            await session.close()

    return requeued


async def requeue_summarizing_contents() -> int:
    """
    SUMMARIZING 상태에서 멈춘 콘텐츠를 다시 LLM 큐에 등록한다.
    
    SUMMARIZING 상태는 워커 재시작 시 큐에 작업이 없어졌을 수 있으므로 재등록이 필요합니다.
    SUMMARY_FAILED 상태는 재시도하지 않음 (실패한 작업은 수동으로 재시도해야 함).

    Returns:
        재큐잉된 콘텐츠 개수
    """
    # SUMMARIZING 상태만 재큐잉 (SUMMARY_FAILED는 제외)
    contents = await _fetch_contents_by_status([
        ContentStatus.SUMMARIZING,
    ])
    if not contents:
        logger.info("No stuck SUMMARIZING contents found.")
        return 0

    requeued = 0

    for content in contents:
        # 이미 큐에 있는 작업은 재큐잉하지 않음 (중복 방지)
        if is_celery_task_in_queue(content_id=content.id, task_name="process_llm_task"):
            logger.debug("LLM job already in queue for content_id=%s, skipping requeue", content.id)
            continue
        
        session = AsyncSessionLocal()
        try:
            repo = ContentRepository(session)
            
            # 재큐잉 로그 추가
            reason = "stuck_summarizing" if content.status == ContentStatus.SUMMARIZING else "retry_summary_failed"
            await repo.add_llm_log(
                content_id=content.id,
                log={"event": "requeued", "reason": reason, "previous_status": content.status.value},
                message="Automatically requeued after restart",
            )
            
            # SUMMARY_FAILED 상태인 경우 SUMMARIZING으로 변경 (재시도)
            # SUMMARIZING 상태는 그대로 유지 (summarize() 함수가 다시 SUMMARIZING으로 설정함)
            if content.status == ContentStatus.SUMMARY_FAILED:
                await repo.update_content_status(content.id, ContentStatus.SUMMARIZING)
            
            await session.commit()

            # Task Queue Adapter 사용 (Celery)
            task_queue = get_task_queue()
            job_id = task_queue.enqueue_llm_job(content_id=content.id)
            requeued += 1
            logger.info("Requeued LLM job for content_id=%s (previous_status=%s, job_id=%s)", 
                      content.id, content.status.value, job_id)
        except Exception as exc:
            logger.exception("Failed to requeue LLM job for content_id=%s", content.id)
            await session.rollback()
        finally:
            await session.close()

    return requeued


