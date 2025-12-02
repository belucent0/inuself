from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..db.models import ContentStatus
from ..repositories.content_repository import ContentRepository
from ..worker.llm_summarizer import summarize_transcription, sanitize_summary_output


class LlmSummaryService:
    """LLM 요약 실행 및 DB 반영 서비스."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ContentRepository(session)
        self.settings = get_settings()

    async def summarize(self, content_id: int) -> None:
        content = await self.repo.get_content(content_id)
        if not content:
            raise ValueError(f"Content {content_id} not found")

        # 이미 완료된 콘텐츠는 스킵 (무한 루프 방지)
        if content.status == ContentStatus.COMPLETED:
            if content.summary_md:
                logger.info(
                    "Content %s already completed with summary (length=%d chars), skipping to prevent infinite loop",
                    content_id,
                    len(content.summary_md)
                )
                logger.info(f"[LLM] SKIP Content already completed: content_id={content_id}, summary_length={len(content.summary_md)}")
                return
            else:
                # 상태는 COMPLETED인데 summary_md가 없는 경우는 이상하지만 재처리
                logger.warning(
                    "Content {} status is COMPLETED but summary_md is empty, reprocessing",
                    content_id
                )
                logger.warning(f"[LLM] WARNING Status is COMPLETED but summary_md is empty, reprocessing: content_id={content_id}")

        transcription = content.transcription or {}
        transcript_text = str(transcription.get("text") or "").strip()
        if not transcript_text:
            raise ValueError("Transcription text is empty, cannot summarize.")

        # 이미 SUMMARIZING 상태인 경우는 재시도 케이스 (로그만 추가)
        # 그 외 상태는 SUMMARIZING으로 변경
        if content.status != ContentStatus.SUMMARIZING:
            await self.repo.update_content_status(content_id, ContentStatus.SUMMARIZING)
        
        await self.repo.add_llm_log(
            content_id,
            log={"event": "summarizing_started", "previous_status": content.status.value},
            message="LLM summarization started",
        )
        await self.session.commit()
        logger.info(
            "LLM summarization started for content_id={} (provider={})",
            content_id,
            self.settings.llm_provider,
        )

        start = time.perf_counter()
        try:
            title, summary_md = summarize_transcription(transcript_text)
            summary_md = sanitize_summary_output(summary_md, transcript_text)
        except Exception as exc:
            await self.repo.update_content_status(content_id, ContentStatus.SUMMARY_FAILED)
            await self.repo.add_llm_log(
                content_id,
                log={"event": "summarizing_failed", "error": str(exc)},
                message="LLM summarization failed",
            )
            await self.session.commit()
            logger.exception("LLM summarization failed for content_id={}", content_id)
            raise

        elapsed = time.perf_counter() - start

        # 제목과 요약 저장
        await self.repo.update_title(content_id, title)
        await self.repo.update_summary_markdown(content_id, summary_md)
        logger.info("Title and summary stored for content_id={}: title={}, summary_length={}", 
                    content_id, title, len(summary_md))
        await self.repo.update_content_status(content_id, ContentStatus.COMPLETED)
        await self.repo.add_llm_log(
            content_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for content_id={} ({:.2f}s)", content_id, elapsed)

