from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..worker.llm_summarizer import summarize_transcription, sanitize_summary_output


class LlmSummaryService:
    """LLM 요약 실행 및 DB 반영 서비스."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.settings = get_settings()

    async def summarize(self, file_id: int) -> None:
        """
        LLM 요약 수행.
        
        file_id는 file 테이블의 id입니다.
        """
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            logger.warning("[LLM] File not found, skipping summarization: file_id=%s", file_id)
            # 존재하지 않는 파일은 실패로 마킹하고 로그를 남긴 뒤 종료
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarization_skipped", "reason": "file_not_found"},
                message="LLM summarization skipped: file not found",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
            await self.session.commit()
            return
        
        await self._summarize_file(file_obj)

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
            # 빈 텍스트인 경우 요약을 건너뛰고 완료 상태로 설정
            logger.warning(
                "Text to summarize is empty (no transcription or OCR text) for file_id=%s. "
                "Skipping summarization and marking as completed.",
                file_id
            )
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarization_skipped", "reason": "empty_text"},
                message="LLM summarization skipped: no text to summarize",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.COMPLETED)
            await self.session.commit()
            return
        
        # 실제 LLM 처리 직전까지 SUMMARY_QUEUED 상태 유지
        # 락 획득 및 서버 시작은 summarize_transcription 내부에서 처리됨
        logger.info("Starting LLM summarization for file_id={} (provider={})", file_id, self.settings.llm_provider)
        
        start = time.perf_counter()
        try:
            # 상태 변경: SUMMARY_QUEUED → SUMMARIZING (실제 LLM 호출 직전)
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarizing_started", "previous_status": file_obj.status.value},
                message="LLM summarization started (about to call LLM)",
            )
            await self.session.commit()
            logger.info("Status changed to SUMMARIZING for file_id={}", file_id)
            
            # 이제 실제 LLM 호출 (락 획득 및 서버 시작 포함)
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


