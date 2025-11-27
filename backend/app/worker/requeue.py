from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from ..core.config import get_settings
from ..db.models import Content, ContentStatus
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository
from .llm_queue import enqueue_llm_job
from .queue import enqueue_transcription_job

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

            enqueue_transcription_job(
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

    Returns:
        재큐잉된 콘텐츠 개수
    """
    contents = await _fetch_contents_by_status([ContentStatus.SUMMARIZING])
    if not contents:
        logger.info("No stuck SUMMARIZING contents found.")
        return 0

    requeued = 0

    for content in contents:
        session = AsyncSessionLocal()
        try:
            repo = ContentRepository(session)
            await repo.add_llm_log(
                content_id=content.id,
                log={"event": "requeued", "reason": "stuck_summarizing"},
                message="Automatically requeued after restart",
            )
            await session.commit()

            enqueue_llm_job(content_id=content.id)
            requeued += 1
            logger.info("Requeued LLM job for content_id=%s", content.id)
        except Exception as exc:
            logger.exception("Failed to requeue LLM job for content_id=%s", content.id)
            await session.rollback()
        finally:
            await session.close()

    return requeued


