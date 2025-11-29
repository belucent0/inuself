from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..db.models import ContentStatus
from ..repositories.content_repository import ContentRepository
from ..worker.llm_summarizer import summarize_transcription, sanitize_summary_output

logger = logging.getLogger(__name__)


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
                from ..worker.utils import safe_print
                safe_print(f"[LLM] SKIP 이미 완료된 콘텐츠: content_id={content_id}, summary_length={len(content.summary_md)}")
                return
            else:
                # 상태는 COMPLETED인데 summary_md가 없는 경우는 이상하지만 재처리
                logger.warning(
                    "Content %s status is COMPLETED but summary_md is empty, reprocessing",
                    content_id
                )
                from ..worker.utils import safe_print
                safe_print(f"[LLM] WARNING 상태는 COMPLETED인데 summary_md가 없음, 재처리: content_id={content_id}")

        transcription = content.transcription or {}
        transcript_text = str(transcription.get("text") or "").strip()
        if not transcript_text:
            raise ValueError("전사 텍스트가 비어 있어 요약할 수 없습니다.")

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
            "LLM summarization started for content_id=%s (provider=%s)",
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
            logger.exception("LLM summarization failed for content_id=%s", content_id)
            raise

        elapsed = time.perf_counter() - start

        # 제목과 요약 저장
        await self.repo.update_title(content_id, title)
        await self.repo.update_summary_markdown(content_id, summary_md)
        logger.info("Title and summary stored for content_id=%s: title=%s, summary_length=%d", 
                    content_id, title, len(summary_md))
        await self.repo.update_content_status(content_id, ContentStatus.COMPLETED)
        await self.repo.add_llm_log(
            content_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for content_id=%s (%.2fs)", content_id, elapsed)

