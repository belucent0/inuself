from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..repositories.content_repository import ContentRepository  # 하위 호환성
from ..worker.llm_summarizer import summarize_transcription, sanitize_summary_output


class LlmSummaryService:
    """LLM 요약 실행 및 DB 반영 서비스."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.content_repo = ContentRepository(session)  # 하위 호환성
        self.settings = get_settings()

    async def summarize(self, content_id: int) -> None:
        """
        LLM 요약 수행.
        
        content_id는 file_id로도 사용됨 (하위 호환성).
        File이 있으면 File 사용, 없으면 Content 사용.
        """
        # 먼저 File로 시도
        file_obj = await self.file_repo.get_file(content_id)
        
        if file_obj:
            # File 사용
            await self._summarize_file(file_obj)
        else:
            # 하위 호환성: Content 사용
            content = await self.content_repo.get_content(content_id)
            if not content:
                raise ValueError(f"Content or File {content_id} not found")
            await self._summarize_content(content, content_id)

    async def _summarize_file(self, file_obj) -> None:
        """File을 사용한 요약."""
        file_id = file_obj.id
        
        # 이미 완료된 파일은 스킵
        if file_obj.status == FileStatus.COMPLETED:
            if file_obj.summary_md:
                logger.info(
                    "File %s already completed with summary (length=%d chars), skipping",
                    file_id,
                    len(file_obj.summary_md)
                )
                return
        
        # 텍스트 추출 (타입에 따라)
        text_to_summarize = ""
        if file_obj.content_type == ContentType.AUDIO:
            transcription = await self.transcription_repo.get_by_file_id(file_id)
            if transcription:
                transcript_data = transcription.transcription or {}
                text_to_summarize = str(transcript_data.get("text") or "").strip()
        elif file_obj.content_type == ContentType.DOCUMENT:
            document = await self.document_repo.get_by_file_id(file_id)
            if document:
                text_to_summarize = document.ocr_text.strip()
        
        if not text_to_summarize:
            raise ValueError("Text to summarize is empty (no transcription or OCR text).")
        
        # 상태 변경
        if file_obj.status != FileStatus.SUMMARIZING:
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
        
        await self.file_repo.add_llm_log(
            file_id,
            log={"event": "summarizing_started", "previous_status": file_obj.status.value},
            message="LLM summarization started",
        )
        await self.session.commit()
        logger.info("LLM summarization started for file_id={} (provider={})", file_id, self.settings.llm_provider)
        
        start = time.perf_counter()
        try:
            title, summary_md = summarize_transcription(text_to_summarize)
            summary_md = sanitize_summary_output(summary_md, text_to_summarize)
        except Exception as exc:
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarizing_failed", "error": str(exc)},
                message="LLM summarization failed",
            )
            await self.session.commit()
            logger.exception("LLM summarization failed for file_id={}", file_id)
            raise
        
        elapsed = time.perf_counter() - start
        
        # 제목과 요약 저장
        await self.file_repo.update_title(file_id, title)
        await self.file_repo.update_summary_markdown(file_id, summary_md)
        logger.info("Title and summary stored for file_id={}: title={}, summary_length={}", 
                    file_id, title, len(summary_md))
        await self.file_repo.update_file_status(file_id, FileStatus.COMPLETED)
        await self.file_repo.add_llm_log(
            file_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for file_id={} ({:.2f}s)", file_id, elapsed)

    async def _summarize_content(self, content, content_id: int) -> None:
        """Content를 사용한 요약 (하위 호환성)."""
        from ..db.models import ContentStatus
        
        # 이미 완료된 콘텐츠는 스킵
        if content.status == ContentStatus.COMPLETED:
            if content.summary_md:
                logger.info(
                    "Content %s already completed with summary (length=%d chars), skipping",
                    content_id,
                    len(content.summary_md)
                )
                return
        
        transcription = content.transcription or {}
        transcript_text = str(transcription.get("text") or "").strip()
        if not transcript_text:
            raise ValueError("Transcription text is empty, cannot summarize.")
        
        if content.status != ContentStatus.SUMMARIZING:
            await self.content_repo.update_content_status(content_id, ContentStatus.SUMMARIZING)
        
        await self.content_repo.add_llm_log(
            content_id,
            log={"event": "summarizing_started", "previous_status": content.status.value},
            message="LLM summarization started",
        )
        await self.session.commit()
        logger.info("LLM summarization started for content_id={} (provider={})", content_id, self.settings.llm_provider)
        
        start = time.perf_counter()
        try:
            title, summary_md = summarize_transcription(transcript_text)
            summary_md = sanitize_summary_output(summary_md, transcript_text)
        except Exception as exc:
            await self.content_repo.update_content_status(content_id, ContentStatus.SUMMARY_FAILED)
            await self.content_repo.add_llm_log(
                content_id,
                log={"event": "summarizing_failed", "error": str(exc)},
                message="LLM summarization failed",
            )
            await self.session.commit()
            logger.exception("LLM summarization failed for content_id={}", content_id)
            raise
        
        elapsed = time.perf_counter() - start
        
        await self.content_repo.update_title(content_id, title)
        await self.content_repo.update_summary_markdown(content_id, summary_md)
        logger.info("Title and summary stored for content_id={}: title={}, summary_length={}", 
                    content_id, title, len(summary_md))
        await self.content_repo.update_content_status(content_id, ContentStatus.COMPLETED)
        await self.content_repo.add_llm_log(
            content_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for content_id={} ({:.2f}s)", content_id, elapsed)

