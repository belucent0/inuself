"""LLM 요약 서비스.

worker/pipelines/llm/summarizer.py의 summarize_transcription()을 사용합니다.
이 함수는 llama-server를 시작/종료하며, 청킹 및 통합 요약을 처리합니다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository

# worker 모듈 접근을 위한 경로 추가
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def sanitize_summary_output(summary_md: str, original_text: str) -> str:
    """요약 결과를 정리합니다.
    
    Args:
        summary_md: LLM이 생성한 요약
        original_text: 원본 텍스트
        
    Returns:
        정리된 요약 텍스트
    """
    if not summary_md:
        return ""
    
    # 프롬프트/지시사항 제거
    lines = summary_md.split('\n')
    cleaned_lines = []
    skip_next = False
    
    for line in lines:
        line = line.strip()
        
        # 지시사항 라인 제거
        if skip_next:
            skip_next = False
            continue
            
        # 프롬프트 제거 패턴
        if any(pattern in line for pattern in [
            "당신은",
            "요약하십시오",
            "마크다운 형식으로",
            "프롬프트는 절대 포함하지",
            "지시사항은 절대 포함하지",
            "## 지시사항",
            "## 프롬프트"
        ]):
            if ":" in line:
                skip_next = True
                continue
            
        if line:
            cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)


def _call_llm_api(settings, text: str) -> str:
    """llama.cpp 서버 API를 직접 호출합니다."""
    import httpx
    
    messages = [
        {"role": "system", "content": settings.llm_system_prompt},
        {"role": "user", "content": f"다음 텍스트를 요약해 주세요:\n\n{text[:10000]}"}
    ]
    
    base_url = settings.llm_api_base_url.rstrip("/")
    model_name = settings.llm_api_model_name
    url = f"{base_url}/v1/chat/completions"
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens
    }
    
    with httpx.Client(timeout=300.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
    
    choices = result.get("choices")
    if not choices:
        return ""
    
    message = choices[0].get("message") or {}
    return message.get("content", "").strip()


def summarize_transcription(text: str) -> tuple[str, str]:
    """전사 텍스트를 요약합니다.
    
    worker/pipelines/llm/summarizer.py의 summarize_transcription()을 사용합니다.
    이 함수는 llama-server를 시작/종료하며, 청킹 및 통합 요약을 처리합니다.
    
    Args:
        text: 요약할 텍스트
        
    Returns:
        (제목, 요약된 텍스트) - worker 모듈과 동일한 순서
    """
    try:
        # worker 모듈의 summarizer 사용 (llama-server 시작/종료 포함)
        from worker.pipelines.llm.summarizer import summarize_transcription as worker_summarize
        
        # worker의 summarize_transcription은 (title, summary_md)를 반환
        title, summary_md = worker_summarize(text)
        
        return title, summary_md
    except Exception as exc:
        return f"요약 실패: {str(exc)}", ""


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
        elif file_obj.content_type in [ContentType.DOCUMENT, ContentType.PORTRAY]:
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
            # DB 상태 업데이트는 llm_task.py에서 마지막 재시도 실패 시에만 수행
            # 여기서는 예외만 로깅하고 다시 던짐
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


