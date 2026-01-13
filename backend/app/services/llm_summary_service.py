"""LLM 요약 서비스.

백엔드에서 LiteLLM 프록시를 직접 호출하여 LLM 요약을 수행합니다.
LiteLLM이 GPU/NPU 자원 상태에 따라 자동으로 라우팅합니다.
"""
from __future__ import annotations

import json
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings, Settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from .litellm_client import request_litellm_completion, LiteLLMClientError


DEFAULT_SUMMARY_PROMPT = """당신은 회의록을 요약하는 전문가입니다. 주어진 전사 내용을 분석하여 핵심만 추출한 요약을 생성하세요.

⚠️ 중요: 절대 전사 내용을 그대로 반환하지 마세요. 반드시 요약만 생성하세요.

1. (필수) title: 회의의 핵심 주제를 담은 제목을 생성하세요. 제목은 반드시 한글로 작성하세요.

2. summary: 전사 내용을 요약하여 마크다운 형식으로 작성하세요.
   - 모든 내용은 반드시 한글로 작성하세요
   - `## 요약` 제목으로 시작하세요
   - 핵심 내용만 불릿 포인트(-)로 간결하게 제공하세요
   - `## 세부 사항` 섹션에 중요한 결정 사항이나 액션 아이템을 번호(1., 2., ...)로 나열하세요

---

다음 JSON 형식으로만 응답하세요:

{{
    "title": "회의 제목",
    "summary": "## 요약\\n\\n- 핵심 내용 1\\n- 핵심 내용 2\\n\\n## 세부 사항\\n\\n1. 결정 사항 1"
}}

전사 내용:
{transcript}
"""


def sanitize_summary_output(summary_md: str, original_text: str) -> str:
    """요약 결과를 정리합니다."""
    if not summary_md:
        return ""
    
    # 프롬프트/지시사항 제거
    lines = summary_md.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line_stripped = line.strip()
        
        # 프롬프트 제거 패턴
        if any(pattern in line_stripped for pattern in [
            "당신은",
            "요약하십시오",
            "마크다운 형식으로",
            "프롬프트는 절대 포함하지",
            "## 지시사항",
            "## 프롬프트"
        ]):
            continue
            
        if line_stripped:
            cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)


def _parse_json_response(raw_response: str, transcript_text: str) -> tuple[str, str]:
    """LLM 응답에서 JSON을 파싱하여 title과 summary를 추출합니다."""
    # ```json ... ``` 블록 찾기
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # ``` 없이 JSON만 있는 경우
        start_idx = raw_response.find('{')
        if start_idx >= 0:
            # 중괄호 균형 맞추기
            brace_count = 0
            end_idx = start_idx
            for i in range(start_idx, len(raw_response)):
                if raw_response[i] == '{':
                    brace_count += 1
                elif raw_response[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break
            
            if end_idx > start_idx:
                json_str = raw_response[start_idx:end_idx + 1]
            else:
                # JSON 파싱 실패
                return _extract_title_fallback(raw_response, transcript_text), raw_response
        else:
            return _extract_title_fallback(raw_response, transcript_text), raw_response
    
    try:
        data = json.loads(json_str)
        title = str(data.get("title", "")).strip()
        summary_md = str(data.get("summary", "")).strip()
        
        if not title:
            title = _extract_title_fallback(summary_md or raw_response, transcript_text)
        
        if not summary_md:
            summary_md = raw_response
        
        return title, summary_md
        
    except json.JSONDecodeError as e:
        logger.warning("JSON parsing failed: %s", e)
        return _extract_title_fallback(raw_response, transcript_text), raw_response


def _extract_title_fallback(summary_md: str, transcript_text: str) -> str:
    """제목 추출 실패 시 대체 방법으로 제목 생성."""
    # 요약의 첫 번째 헤더 추출
    lines = summary_md.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title and len(title) <= 100:
                return title
        elif line.startswith("## "):
            title = line[3:].strip()
            if title and len(title) <= 100:
                return title
    
    # 전사 텍스트의 첫 문장 사용
    first_sentence = transcript_text.split(".")[0].strip()
    if first_sentence:
        if len(first_sentence) > 100:
            first_sentence = first_sentence[:97] + "..."
        return first_sentence
    
    return "회의록"


def summarize_transcription(text: str) -> tuple[str, str]:
    """전사 텍스트를 요약합니다.
    
    LiteLLM 프록시를 통해 GPU/NPU로 자동 라우팅됩니다.
    
    Args:
        text: 요약할 텍스트
        
    Returns:
        (제목, 요약된 텍스트) 튜플
    """
    settings = get_settings()
    
    normalized = text.strip()
    if not normalized:
        raise ValueError("Transcription text to summarize is empty.")
    
    # 텍스트가 너무 길면 앞부분만 사용 (청킹은 향후 구현)
    max_chars = 30000  # 약 10000 토큰
    if len(normalized) > max_chars:
        logger.warning("Text too long (%d chars), truncating to %d chars", len(normalized), max_chars)
        normalized = normalized[:max_chars]
    
    prompt = DEFAULT_SUMMARY_PROMPT.format(transcript=normalized)
    messages = [
        {"role": "system", "content": settings.llm_system_prompt},
        {"role": "user", "content": prompt},
    ]
    
    try:
        if settings.llm_provider == "litellm":
            raw_response = request_litellm_completion(
                settings=settings,
                messages=messages,
            )
        else:
            # 기존 provider 지원 (fallback)
            logger.warning("Non-litellm provider '%s' not supported in backend, use litellm", settings.llm_provider)
            raise ValueError(f"Backend only supports 'litellm' provider, got: {settings.llm_provider}")
        
        title, summary_md = _parse_json_response(raw_response, normalized)
        summary_md = sanitize_summary_output(summary_md, normalized)
        
        return title, summary_md
        
    except LiteLLMClientError as exc:
        logger.error("LLM summarization failed: %s", exc)
        raise RuntimeError(f"LLM summarization failed: {exc}") from exc


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


