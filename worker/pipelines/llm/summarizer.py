from __future__ import annotations

import logging
import re
import uuid
from typing import Literal
from enum import Enum

import httpx

from worker.config import get_settings
from .llamacpp_client import LlamaServerClientError, request_chat_completion
from .litellm_client import LiteLLMClientError, request_litellm_completion
from worker.utils.resource_client import (
    acquire_resource,
    release_resource,
    ResourceAcquisitionError,
    select_resource_type_dynamic,
)

logger = logging.getLogger(__name__)


class DocumentType(Enum):
    MEETING = "meeting"
    LECTURE = "lecture"
    INTERVIEW = "interview"
    GENERAL = "general"


def detect_document_type(text: str) -> DocumentType:
    """전사 텍스트의 문서 유형을 감지합니다."""
    text_lower = text.lower()
    
    meeting_indicators = [
        "회의", "회의실", "참석자", "안건", "상정", "발언", "의결",
        "meeting", "conference", "attendees", "agenda", "motion",
        "승인", "의견", "토론", "발표자", "주관", "공동", "참가자"
    ]
    
    lecture_indicators = [
        "강의", "강연", "수업", "트레이닝", "워크숍", "세미나", "컨퍼런스",
        "lecture", "class", "training", "workshop", "seminar",
        "교수", "강사", "讲师", "教授", "선생님", "학습", "과제"
    ]
    
    interview_indicators = [
        "인터뷰", "취업", "면접", "지원자", "질문", "답변",
        "interview", "applicant", "candidate", "question", "answer",
        "지원동기", "경력", "성취", "성장"
    ]
    
    meeting_count = sum(1 for indicator in meeting_indicators if indicator in text_lower)
    lecture_count = sum(1 for indicator in lecture_indicators if indicator in text_lower)
    interview_count = sum(1 for indicator in interview_indicators if indicator in text_lower)
    
    max_count = max(meeting_count, lecture_count, interview_count)
    
    if max_count == 0:
        return DocumentType.GENERAL
    elif meeting_count == max_count:
        return DocumentType.MEETING
    elif lecture_count == max_count:
        return DocumentType.LECTURE
    else:
        return DocumentType.INTERVIEW


PROMPT_TEMPLATES = {
    DocumentType.MEETING: """당신은 전문적인 회의록 작성자입니다. 주어진 회의 전사 내용을 분석하여 체계적이고 포괄적인 회의록을 작성하세요.

## 작성 지침

1. **객관성**: 발언 내용을 해석하거나 해석하지 말고 핵심을 추출하세요
2. **구조화**: 회의의 흐름에 따라 논리적으로 구성하세요
3. **완전성**: 중요한 논의사항, 결정사항, 액션 아이템을 빠짐없이 기록하세요
4. **간결성**: 불필요한 반복이나phantasmagorical 표현은 제거하세요

## 필수 포함 섹션

다음 형식으로 마크다운을 작성하세요:

```markdown
## Executive Summary
- 회의의 목적과 핵심 결론을 2-3문장으로 요약
- 주요 참석자 그룹 언급
- 가장 중요한 결정 또는 결과 강조

## 회의 개요
- **일시**: (전사에서 시간 언급이 있다면)
- **주제**: 회의 주제 정리
- **참석자**: (전사에서 참석자 언급이 있다면)
- **진행자**: (전사에서 진행자 언급이 있다면)

## 주요 논의 사항
### Topic 1
- 논의된 핵심 내용 요약
- 다양한 의견이 있었다면 간략히 언급

### Topic 2
- 논의된 핵심 내용 요약
- 결론이 있다면 함께 기록

## 결정 사항
1. **[결정 1]**: 명확하게 기록
2. **[결정 2]**: 명확하게 기록
- 결정의 근거가 있다면 간략히 포함

## 액션 아이템
- [ ] **[담당자]**: 실행해야 할 작업 (기한이 언급되었다면 포함)
- [ ] **[담당자]**: 실행해야 할 작업

## 핵심 키워드
- 키워드 1, 키워드 2, 키워드 3

## 톤 및 분위기
- 회의의 전반적인 톤 (예: 협력적, 논쟁적, 건설적 등)
- 참여자들의 태도 언급
```

## 전사 내용:
{transcript}

## 출력 요구사항
- 위 형식을 정확히 따라주세요
- 모든 섹션을 포함하고 채워주세요
- 정보가 없는 섹션에는 "정보 없음" 대신 관련 내용을 유추하거나 "해당 사항 없음"이라고 명시
- 마크다운 형식을 정확히 유지하세요""",
    
    DocumentType.LECTURE: """당신은 교육 콘텐츠 분석 전문가입니다. 강의/강연 전사 내용을 분석하여 학습에 최적화된 포괄적인 요약을 작성하세요.

## 작성 지침

1. **교육적 관점**: 학습자가 핵심 개념을 이해할 수 있도록 구조화하세요
2. **계층적 정리**: 기본 개념 → 심화 개념 순으로 조직하세요
3. **예시 보존**: 중요한 예시나 비유는 요약에 포함하세요
4. **학습 포인트**: 시험에 나올 만한 핵심 포인트를 표시하세요

## 필수 포함 섹션

```markdown
## Executive Summary
- 강의의 핵심 주제와 학습 목표 요약
- 이 강의에서 다루는 주요 개념 개요
- 학습 후 얻을 수 있는 역량 언급

## 강의 개요
- **주제**: 강의 주제
- **강사**: (전사에서 언급되었다면)
- **대상**: (전사에서 언급되었다면)
- **강의 시간**: (전사에서 시간 언급이 있다면)

## 핵심 개념
### 개념 1
- 정의와 핵심 설명
- 왜 중요한지 설명

### 개념 2
- 정의와 핵심 설명
- 관련 예시 (있는 경우)

## 주요 내용 정리
### Section 1
- 핵심 내용 요약
- 중요한 세부사항

### Section 2
- 핵심 내용 요약
- 중요한 세부사항

## 중요 포인트 (학습 노트)
- ★ **필수 암기**: 핵심 공식, 정의, 원칙
- ★ **응용 팁**: 실제 적용 방법
- ★ **흔한 오해**: 주의할 점

## 실습/예시
- 강의에서 나온 중요한 예시 정리
- 연습 문제나 코드가 있다면 포함

## 추가 학습 자료
- 이 강의와 관련된 심화 주제
- 참고할 만한 개념

## 핵심 키워드
- 키워드 1, 키워드 2, 키워드 3

## 이해도 체크
- 강의에서 나온 핵심 질문 2-3개
- 학습자가 스스로 확인 가능하도록
```

## 전사 내용:
{transcript}

## 출력 요구사항
- 위 형식을 정확히 따라주세요
- 모든 섹션을 포함하고 채워주세요
- 마크다운 형식을 정확히 유지하세요""",
    
    DocumentType.INTERVIEW: """당신은 인터뷰 분석 전문가입니다. 인터뷰 전사 내용을 분석하여 체계적인 인터뷰 리포트를 작성하세요.

## 작성 지침

1. **구조화된 분석**: 질문-답변 패턴을 분석하여 핵심 정보를 추출하세요
2. **평가적 관점**: 지원자/피면접자의 강점과 약점을 분석하세요
3. **역량 중심**: 채용/평가에 필요한 역량별로 정보를 정리하세요
4. **객관성**: 주관적 해석보다 사실 기반 분석을 우선하세요

## 필수 포함 섹션

```markdown
## Executive Summary
- 인터뷰의 목적과 맥락 요약
- 전반적인 평가 개요 (긍정적/중립적/검토 필요)
- 핵심 발견사항 2-3가지

## 인터뷰 개요
- **목적**: 인터뷰 목적 (채용, 이직, 평가 등)
- **직무/역할**: 대상 직무나 역할
- **일시**: (전사에서 시간 언급이 있다면)
- **면접관**: (전사에서 언급되었다면)
- **지원자/피면접자**: (전사에서 언급되었다면)

## 질문별 분석
### Q1: [질문 주제]
- **질문 내용**: 
- **답변 요약**: 
- **평가**: 

### Q2: [질문 주제]
- **질문 내용**: 
- **답변 요약**: 
- **평가**: 

## 역량별 분석
### 역량 1 (예: 전문성)
- 발현된 역량 수준: 높음/중간/낮음
- 근거가 되는 답변/행동:
- 개선점 제안:

### 역량 2 (예: 소통 능력)
- 발현된 역량 수준: 높음/중간/낮음
- 근거가 되는 답변/행동:
- 개선점 제안:

## 강점
1. **[강점 1]**: 구체적 근거
2. **[강점 2]**: 구체적 근거

## 개선 영역
1. **[개선 영역 1]**: 구체적 제안
2. **[개선 영역 2]**: 구체적 제안

## 핵심 키워드
- 키워드 1, 키워드 2, 키워드 3

## 총평 및 추천 의견
- 전반적인 인상과 적합성 평가
- 채용/진행 결정에 대한 권고
```

## 전사 내용:
{transcript}

## 출력 요구사항
- 위 형식을 정확히 따라주세요
- 모든 섹션을 포함하고 채워주세요
- 마크다운 형식을 정확히 유지하세요""",
    
    DocumentType.GENERAL: """당신은 전문적인 콘텐츠 분석가입니다. 주어진 전사 내용을 분석하여 체계적이고 이해하기 쉬운 요약을 작성하세요.

## 작성 지침

1. **구조화**: 내용을 논리적 섹션으로 구성하세요
2. **핵심 추출**: 가장 중요한 정보를 우선순위로 정리하세요
3. **가독성**: 스캔하기 쉽도록 명확한 구조를 사용하세요
4. **중요도 표시**: 핵심 정보는 강조하여 표시하세요

## 필수 포함 섹션

```markdown
## Executive Summary
- 콘텐츠의 핵심 메시지 2-3문장으로 요약
- 가장 중요한 포인트 강조
- 이 콘텐츠의 목적이나 배경

## 개요
- 주제/내용 개요
- 배경 정보 (있는 경우)
- 대상 독자/청취자 (알려진 경우)

## 주요 내용
### Section 1
- 핵심 내용 요약
- 중요한 세부사항

### Section 2
- 핵심 내용 요약
- 중요한 세부사항

## 핵심 포인트
- ★ 가장 중요한 정보 3-5가지
- 각 포인트의 중요성 한 줄 설명

## 세부 사항
- 주요 내용을 뒷받침하는 세부 정보
- 숫자, 날짜, 구체적 사실들

## 결론 및 함의
- 콘텐츠의 결론이나 최종 메시지
- 이 정보가 의미하는 바

## 핵심 키워드
- 키워드 1, 키워드 2, 키워드 3
```

## 전사 내용:
{transcript}

## 출력 요구사항
- 위 형식을 정확히 따라주세요
- 모든 섹션을 포함하고 채워주세요
- 마크다운 형식을 정확히 유지하세요""",
}


SUMMARY_SYSTEM_PROMPT = """당신은 전문적인 콘텐츠 분석가이자 회의록 작성자입니다.
- 모든 출력은 반드시 한글로 작성하세요
- 객관적이고 명확한 표현을 사용하세요
- 정보가 불충분한 경우 추측하지 말고 "해당 사항 없음" 또는 "정보 없음"이라고 명시하세요
- 마크다운 형식을 정확히 따라야 합니다"""


def _split_text_into_chunks(text: str, max_tokens_per_chunk: int = 10000, overlap_tokens: int = 500) -> list[str]:
    """텍스트를 지정된 토큰 수 단위로 청크로 분할합니다."""
    chars_per_token = 3.5
    max_chunk_chars = int(max_tokens_per_chunk * chars_per_token)
    overlap_chars = int(overlap_tokens * chars_per_token)
    
    if len(text) <= max_chunk_chars:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + max_chunk_chars
        
        if end < len(text):
            last_period = text.rfind('.', start, end)
            last_newline = text.rfind('\n', start, end)
            last_exclamation = text.rfind('!', start, end)
            last_question = text.rfind('?', start, end)
            
            sentence_end = max(last_period, last_newline, last_exclamation, last_question)
            if sentence_end > start + max_chunk_chars * 0.7:
                end = sentence_end + 1
        
        chunk = text[start:end]
        chunks.append(chunk)
        
        start = end - overlap_chars
        if start >= len(text):
            break
        if start < 0:
            start = 0
    
    return chunks


def _summarize_chunk_with_llm(chunk: str, chunk_index: int, total_chunks: int, prompt_template: str, settings) -> str:
    """단일 청크를 OpenAI 호환 API 서버로 요약합니다."""
    prompt = prompt_template.format(transcript=chunk)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    
    try:
        raw_response = request_chat_completion(settings=settings, messages=messages, stream=False)
        raw_response = raw_response.strip()
        if not raw_response:
            raise RuntimeError("LLM API summarization result is empty.")
        logger.info("Chunk %d/%d response completed (length: %d chars)", chunk_index, total_chunks, len(raw_response))
        return raw_response
    except LlamaServerClientError as exc:
        error_str = str(exc)
        if "context" in error_str.lower() or "token" in error_str.lower() or "overflow" in error_str.lower():
            raise RuntimeError(f"LLM API context length exceeded (chunk {chunk_index}/{total_chunks}): {exc}")
        raise RuntimeError(f"LLM API summarization failed (chunk {chunk_index}/{total_chunks}): {exc}")


def _summarize_chunk_with_litellm(chunk: str, chunk_index: int, total_chunks: int, prompt_template: str, settings) -> str:
    """단일 청크를 LiteLLM 프록시로 요약합니다."""
    prompt = prompt_template.format(transcript=chunk)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    
    try:
        raw_response = request_litellm_completion(settings=settings, messages=messages)
        raw_response = raw_response.strip()
        if not raw_response:
            raise RuntimeError("LiteLLM summarization result is empty")
        logger.info("Chunk %d/%d via LiteLLM completed (length: %d chars)", chunk_index, total_chunks, len(raw_response))
        return raw_response
    except LiteLLMClientError as exc:
        raise RuntimeError(f"LiteLLM summarization failed (chunk {chunk_index}/{total_chunks}): {exc}")


def summarize_text(text: str, on_resource_acquired: callable = None) -> str:
    """
    전사 텍스트를 전문적으로 요약합니다.

    Args:
        text: 요약할 전사 텍스트
        on_resource_acquired: 리소스 획득 후 호출할 콜백 (UI 상태 업데이트용)

    Returns:
        전문적으로 구성된 요약 마크다운
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("Transcription text to summarize is empty.")

    settings = get_settings()
    document_type = detect_document_type(normalized)
    logger.info(f"[LLM Summarizer] Detected document type: {document_type.value}")

    prompt_template = PROMPT_TEMPLATES[document_type]
    
    if settings.llm_provider == "litellm":
        raw_response = _summarize_with_litellm(normalized, prompt_template, settings, on_resource_acquired=on_resource_acquired)
    elif settings.llm_provider in ("llamacpp_server", "flm"):
        if on_resource_acquired:
            try:
                on_resource_acquired()
            except Exception as cb_err:
                logger.warning(f"on_resource_acquired callback failed: {cb_err}")
        raw_response = _summarize_with_llm(normalized, prompt_template, settings)
    else:
        raise ValueError(f"지원하지 않는 LLM provider: {settings.llm_provider}. 'litellm', 'llamacpp_server', 또는 'flm'만 지원됩니다.")
    
    return raw_response


def summarize_transcription(text: str, on_resource_acquired: callable = None) -> tuple[str, str]:
    """
    전사 텍스트를 요약하고 제목을 추출합니다.

    Args:
        text: 요약할 전사 텍스트
        on_resource_acquired: 리소스 획득 후 호출할 콜백 (UI 상태 업데이트용)

    Returns:
        (title, summary_md) 튜플
    """
    summary_md = summarize_text(text, on_resource_acquired=on_resource_acquired)
    title = extract_title(summary_md, text)
    return title, summary_md


def _summarize_with_llm(text: str, prompt_template: str, settings) -> str:
    """OpenAI 호환 API 서버(llama.cpp 서버, LM Studio 등) Chat Completions API를 통한 요약."""
    actual_context = settings.llm_context_length
    logger.info("[LLM Summarizer] Using context length: %d tokens", actual_context)
    
    prompt_overhead = 2500
    available_tokens = actual_context - settings.llm_max_tokens - prompt_overhead
    chars_per_token = 2.5
    max_chunk_chars = int(available_tokens * chars_per_token)
    
    safe_max_chars = int(max_chunk_chars * 0.8)
    if len(text) <= safe_max_chars:
        return _summarize_chunk_with_llm(text, 1, 1, prompt_template, settings)
    
    safe_chunk_tokens = int(available_tokens * 0.6)
    chunks = _split_text_into_chunks(text, max_tokens_per_chunk=safe_chunk_tokens, overlap_tokens=500)
    logger.info("Split text into %d chunks", len(chunks))
    
    successful_chunks = []
    failed_chunks = []
    chunk_errors = []
    
    for i, chunk in enumerate(chunks, 1):
        logger.info("Summarizing chunk %d/%d...", i, len(chunks))
        try:
            chunk_summary = _summarize_chunk_with_llm(chunk, i, len(chunks), prompt_template, settings)
            successful_chunks.append({"chunk_index": i, "summary": chunk_summary})
        except Exception as exc:
            logger.error("Chunk %d summarization failed: %s", i, exc)
            failed_chunks.append(i)
            chunk_errors.append(f"Chunk {i}: {exc}")
    
    if not successful_chunks:
        error_details = "; ".join(chunk_errors[:3])
        raise RuntimeError(f"All chunk summarizations failed. Errors: {error_details}")
    
    if failed_chunks:
        logger.warning("Some chunks failed: %s, succeeded: %d/%d", failed_chunks, len(successful_chunks), len(chunks))
    
    combined_summaries = "\n\n".join([
        f"=== 부분 {cs['chunk_index']} ===\n\n{cs['summary']}"
        for cs in successful_chunks
    ])
    
    failed_parts_note = ""
    if failed_chunks:
        failed_parts_note = f"\n\n참고: 일부 부분({', '.join(map(str, failed_chunks))}) 요약에 실패하여 제외되었습니다."
    
    merge_prompt = f"""당신은 전문적인 콘텐츠 통합 요약가입니다. 다음은 긴 콘텐츠의 여러 부분에 대한 전문 요약입니다.
모든 부분을 통합하여 하나의 일관된 포괄적 요약을 작성하세요.

## 통합 지침
1. 모든 부분의 핵심 내용을 통합하세요
2. 중복 정보를 제거하고 통합하세요
3. 실제 콘텐츠 내용에만 집중하세요
4. 마크다운 형식을 유지하세요{failed_parts_note}

부분별 요약:
{combined_summaries}
"""
    
    try:
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": merge_prompt},
        ]
        raw_response = request_chat_completion(settings=settings, messages=messages, stream=False)
        return raw_response.strip()
    except Exception as exc:
        logger.warning("Merge summarization failed: %s", exc)
        return f"# 전문 요약\n\n{combined_summaries}"


def _summarize_with_litellm(text: str, prompt_template: str, settings, resource_timeout: float = 120.0, on_resource_acquired: callable = None) -> str:
    """LiteLLM 프록시를 통한 LLM 요약."""
    logger.info("[LiteLLM Summarizer] Starting summarization via LiteLLM proxy")
    
    task_id = f"llm-{uuid.uuid4().hex[:8]}"
    resource_type = select_resource_type_dynamic("llm", accuracy_mode="speed")
    resource_info = None
    
    try:
        resource_info = acquire_resource(
            resource_type=resource_type,
            task_type="llm",
            task_id=task_id,
            accuracy_mode="speed",
            timeout=resource_timeout,
        )
        logger.info(f"[LiteLLM] Resource acquired: {resource_info.provider}")
    except ResourceAcquisitionError as e:
        raise RuntimeError(f"LLM resource unavailable: {e}")
    
    if on_resource_acquired:
        try:
            on_resource_acquired()
        except Exception as cb_err:
            logger.warning(f"on_resource_acquired callback failed: {cb_err}")
    
    try:
        actual_context = settings.llm_context_length
        prompt_overhead = 2500
        available_tokens = actual_context - settings.llm_max_tokens - prompt_overhead
        chars_per_token = 2.5
        max_chunk_chars = int(available_tokens * chars_per_token)
        safe_max_chars = int(max_chunk_chars * 0.8)
        
        if len(text) <= safe_max_chars:
            return _summarize_chunk_with_litellm(text, 1, 1, prompt_template, settings)
        
        safe_chunk_tokens = int(available_tokens * 0.6)
        chunks = _split_text_into_chunks(text, max_tokens_per_chunk=safe_chunk_tokens, overlap_tokens=500)
        logger.info("Split text into %d chunks", len(chunks))
        
        successful_chunks = []
        failed_chunks = []
        chunk_errors = []
        
        for i, chunk in enumerate(chunks, 1):
            logger.info("Summarizing chunk %d/%d via LiteLLM...", i, len(chunks))
            try:
                chunk_summary = _summarize_chunk_with_litellm(chunk, i, len(chunks), prompt_template, settings)
                successful_chunks.append({"chunk_index": i, "summary": chunk_summary})
            except Exception as exc:
                logger.error("Chunk %d summarization failed: %s", i, exc)
                failed_chunks.append(i)
                chunk_errors.append(f"Chunk {i}: {exc}")
        
        if not successful_chunks:
            raise RuntimeError(f"All chunk summarizations failed")
        
        combined_summaries = "\n\n".join([
            f"=== 부분 {cs['chunk_index']} ===\n\n{cs['summary']}"
            for cs in successful_chunks
        ])
        
        failed_parts_note = ""
        if failed_chunks:
            failed_parts_note = f"\n\n참고: 일부 부분({', '.join(map(str, failed_chunks))}) 요약에 실패하여 제외되었습니다."
        
        merge_prompt = f"""당신은 전문적인 콘텐츠 통합 요약가입니다. 다음은 긴 콘텐츠의 여러 부분에 대한 전문 요약입니다.
모든 부분을 통합하여 하나의 일관된 포괄적 요약을 작성하세요.

## 통합 지침
1. 모든 부분의 핵심 내용을 통합하세요
2. 중복 정보를 제거하고 통합하세요
3. 실제 콘텐츠 내용에만 집중하세요
4. 마크다운 형식을 유지하세요{failed_parts_note}

부분별 요약:
{combined_summaries}
"""
        
        try:
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": merge_prompt},
            ]
            raw_response = request_litellm_completion(settings=settings, messages=messages)
            return raw_response.strip()
        except Exception as exc:
            logger.warning("Merge summarization via LiteLLM failed: %s", exc)
            return f"# 전문 요약\n\n{combined_summaries}"
    
    finally:
        if resource_info:
            release_resource(resource_type, "llm", task_id)
            logger.info(f"[LiteLLM] Resource released: {resource_type}/llm")


def _is_transcript_echo(summary: str, transcript: str) -> bool:
    """
    요약이 전사 내용을 그대로 반환한 것인지 확인.
    요약이 전사 내용과 너무 유사하면 True 반환.
    """
    if not summary or not transcript:
        return False
    
    # 요약이 전사 내용보다 너무 길면 전사 내용 반복으로 간주
    if len(summary) > len(transcript) * 0.8:
        logger.warning("Summary is 80%% or more of transcript length: summary=%d, transcript=%d", len(summary), len(transcript))
        return True
    
    # 요약의 첫 200자와 전사 내용의 첫 200자가 거의 동일하면 전사 내용 반복으로 간주
    summary_start = summary[:200].strip()
    transcript_start = transcript[:200].strip()
    
    if summary_start and transcript_start:
        # 유사도 계산 (간단한 방법: 공통 단어 비율)
        summary_words = set(summary_start.split())
        transcript_words = set(transcript_start.split())
        
        if len(transcript_words) > 0:
            common_words = summary_words & transcript_words
            similarity = len(common_words) / len(transcript_words)
            
            # 70% 이상 유사하면 전사 내용 반복으로 간주
            if similarity > 0.7:
                logger.warning("Summary is 70%% or more similar to transcript: similarity=%.2f", similarity)
                return True
    
    # 요약에 전사 내용의 긴 문장이 그대로 포함되어 있는지 확인
    # 전사 내용을 문장 단위로 분할
    transcript_sentences = [s.strip() for s in re.split(r'[.!?]\s+', transcript) if len(s.strip()) > 20]
    
    # 요약에 전사 내용의 문장이 3개 이상 그대로 포함되어 있으면 전사 내용 반복으로 간주
    matched_sentences = 0
    for sentence in transcript_sentences[:10]:  # 처음 10개 문장만 확인
        if len(sentence) > 30 and sentence in summary:
            matched_sentences += 1
    
    if matched_sentences >= 3:
        logger.warning("Summary contains %d or more sentences directly from transcript", matched_sentences)
        return True
    
    return False


def _looks_like_prompt_echo(text: str) -> bool:
    cleaned = text.strip().lower()
    if not cleaned:
        return True
    if len(cleaned) < 40:
        return True
    return any(marker in cleaned for marker in PROMPT_ECHO_MARKERS)


def _has_korean_characters(text: str) -> bool:
    """텍스트에 한글이 포함되어 있는지 확인"""
    import re
    return bool(re.search(r'[가-힣]', text))


def _looks_like_invalid_title(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return True
    
    cleaned_lower = cleaned.lower()
    
    # 프롬프트 마커가 포함되어 있으면 무효
    if any(marker in cleaned_lower for marker in TITLE_PROMPT_MARKERS):
        return True
    
    # 영어로만 작성된 긴 텍스트는 프롬프트일 가능성이 높음
    # 한글이 없고 영어 단어가 여러 개 포함되어 있으면 프롬프트로 간주
    if not _has_korean_characters(cleaned):
        # 영어 단어가 5개 이상이면 프롬프트로 간주
        import re
        english_words = re.findall(r'\b[a-z]+\b', cleaned_lower)
        if len(english_words) >= 5:
            return True
        # 특정 프롬프트 패턴이 포함되어 있으면 무효
        prompt_patterns = [
            "the user",
            "user wants",
            "wants a",
            "concise title",
            "reflecting",
            "core subject",
            "characters",
            "or less",
            "no markdown",
            "no quotes",
        ]
        if any(pattern in cleaned_lower for pattern in prompt_patterns):
            return True
    
    # 프롬프트처럼 보이는 긴 설명문이면 무효 (50자 이상이고 특정 패턴 포함)
    if len(cleaned) > 50:
        prompt_patterns = [
            "we need to",
            "they gave",
            "basically says",
            "it's empty",
            "nothing was found",
            "produce a concise",
            "reflecting core topic",
        ]
        if any(pattern in cleaned_lower for pattern in prompt_patterns):
            return True
    
    return False


def _extract_sentences(transcript_text: str) -> list[str]:
    if not transcript_text:
        return []
    normalized = transcript_text.replace("\r", " ")
    raw_sentences = SENTENCE_SPLIT_REGEX.split(normalized)
    sentences: list[str] = []
    for raw in raw_sentences:
        fragment = raw.strip()
        if not fragment:
            continue
        if _looks_like_prompt_echo(fragment):
            continue
        sentences.append(fragment)
    return sentences


def _truncate_sentence(sentence: str, max_len: int = 120) -> str:
    sentence = sentence.strip()
    if len(sentence) <= max_len:
        return sentence
    return sentence[: max_len - 3].rstrip() + "..."


def _build_fallback_summary(transcript_text: str) -> str:
    sentences = _extract_sentences(transcript_text)
    summary_items = sentences[:3]
    if not summary_items:
        summary_items = ["전사 텍스트에서 핵심 내용을 충분히 찾지 못했습니다."]
    detail_items = sentences[3:6]
    if not detail_items:
        detail_items = ["전사 텍스트에서 명확한 결정 사항이나 액션 아이템을 확인하지 못했습니다."]
    
    summary_section = "\n".join(f"- {_truncate_sentence(item)}" for item in summary_items)
    detail_section = "\n".join(
        f"{idx}. {_truncate_sentence(item)}"
        for idx, item in enumerate(detail_items, 1)
    )
    
    return f"""## 요약
{summary_section}

## 세부 사항
{detail_section}"""


PROMPT_ECHO_MARKERS = [
    "you are an expert meeting summarizer",
    "guidelines:",
    "the user wants a title",
    "제목:",
    "please create a unified, comprehensive summary",
    "always answer in rhymes",
    "오늘은 목요일",
    "today is thursday",
    "in rhyme i'll say",
    "the user:",
    "we have a long transcript",
    "the prompt says",
    "the instruction:",
    "we need to produce",
    "they gave a summary",
    "basically says nothing",
    "it's empty",
    "nothing was found",
    "summarize the following",
    "korean transcription",
]

TITLE_PROMPT_MARKERS = [
    "the user wants",
    "user wants",
    "wants a title",
    "wants a concise",
    "concise title",
    "reflecting the core",
    "reflecting core",
    "core subject",
    "50 characters",
    "characters or less",
    "no markdown",
    "no quotes",
    "제목:",
    "요구사항",
    "please create",
    "create a concise",
    "마크다운",
    "we need to produce",
    "they gave a summary",
    "basically says nothing",
    "reflecting core topic",
    "within 50 characters",
    "다음 회의록 요약을 보고",
    "적절한 제목을 하나만",
    "회의의 핵심 주제를 반영",
    "제목만 출력하고",
    "다른 설명은 포함하지",
    "반드시 한글로만",
    "중요:",
]

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[\.!?])\s+|\n+")


def extract_title(summary_md: str, transcript_text: str) -> str:
    """
    요약 텍스트와 전사 텍스트를 기반으로 제목을 추출합니다.
    
    Args:
        summary_md: LLM이 생성한 요약 마크다운 텍스트
        transcript_text: 원본 전사 텍스트
    
    Returns:
        추출된 제목 (최대 512자)
    """
    settings = get_settings()
    
    title_prompt = """다음 전문 요약을 보고 적절한 제목을 한글로 하나만 제시해주세요.

중요:
- 반드시 한글로만 작성하세요
- 요약의 핵심 주제를 반영하는 간결한 제목
- 30자 이내로 작성
- 제목만 출력하고 다른 설명, 영어, 프롬프트 지시사항은 절대 포함하지 마세요
- 마크다운 형식이나 따옴표 없이 순수 한글 텍스트로만 출력

전문 요약:
{summary}

제목:"""

    prompt = title_prompt.format(summary=summary_md[:2000])
    
    try:
        if settings.llm_provider == "litellm":
            title = _extract_title_with_litellm(prompt, settings)
        elif settings.llm_provider in ("llamacpp_server", "flm"):
            title = _extract_title_with_llm(prompt, settings)
        else:
            logger.warning("Title extraction failed with unsupported LLM provider: %s", settings.llm_provider)
            return _extract_title_fallback(summary_md, transcript_text)
        
        title = title.strip()
        
        title_marker = "제목:"
        if title_marker in title:
            parts = title.split(title_marker, 1)
            if len(parts) > 1:
                title = parts[1].strip()
        
        english_prompt_starters = [
            "the user wants",
            "user wants",
            "we need to",
            "they gave",
            "basically says",
        ]
        title_lower = title.lower()
        for starter in english_prompt_starters:
            if title_lower.startswith(starter):
                first_period = title.find(".")
                if first_period > 0:
                    title = title[first_period + 1:].strip()
                else:
                    first_newline = title.find("\n")
                    if first_newline > 0:
                        title = title[first_newline + 1:].strip()
                    else:
                        return _extract_title_fallback(summary_md, transcript_text)
                break
        
        first_period = title.find(".")
        first_newline = title.find("\n")
        if first_period > 0 and (first_newline < 0 or first_period < first_newline):
            potential_title = title[:first_period].strip()
            if not _looks_like_invalid_title(potential_title) and len(potential_title) > 5:
                title = potential_title
        elif first_newline > 0:
            title = title[:first_newline].strip()
        
        if title.startswith("#"):
            title = title.lstrip("#").strip()
        title = title.strip('"\'')
        title = title.replace("\n", " ").strip()
        if len(title) > 512:
            title = title[:509] + "..."
        
        if not _has_korean_characters(title):
            logger.warning("Title does not contain Korean characters, using fallback method: %s", title[:100])
            return _extract_title_fallback(summary_md, transcript_text)
        
        if not title or _looks_like_invalid_title(title):
            return _extract_title_fallback(summary_md, transcript_text)
        
        logger.info("Title extraction completed: %s (length: %d chars)", title, len(title))
        return title
        
    except Exception as exc:
        logger.warning("Title extraction failed, using fallback method: %s", exc)
        return _extract_title_fallback(summary_md, transcript_text)


def _extract_title_with_llm(prompt: str, settings) -> str:
    """OpenAI 호환 API 서버(llama.cpp 서버, FastFlowLM 등)를 사용한 제목 추출."""
    from .llamacpp_client import request_chat_completion
    
    system_prompt = "당신은 전문 요약에서 제목을 추출하는 도우미입니다. 한글로만 제목을 출력하세요. 다른 설명이나 영어는 절대 포함하지 마세요."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    
    title = request_chat_completion(
        settings=settings,
        messages=messages,
        temperature=0.3,
        max_tokens=100,
        stream=False,
    )
    
    title_lower = title.lower()
    if "reasoning" in title_lower or "the user:" in title_lower or title_lower.startswith("the user"):
        if "제목:" in title:
            parts = title.split("제목:", 1)
            if len(parts) > 1:
                title = parts[1].strip()
        elif title_lower.startswith("the user") or title_lower.startswith("user wants"):
            first_period = title.find(".")
            if first_period > 0:
                title = title[first_period + 1:].strip()
            else:
                first_newline = title.find("\n")
                if first_newline > 0:
                    title = title[first_newline + 1:].strip()
    
    return title


def _extract_title_with_litellm(prompt: str, settings) -> str:
    """LiteLLM 프록시를 통한 제목 추출."""
    system_prompt = "당신은 전문 요약에서 제목을 추출하는 도우미입니다. 한글로만 제목을 출력하세요. 다른 설명이나 영어는 절대 포함하지 마세요."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    
    title = request_litellm_completion(
        settings=settings,
        messages=messages,
        temperature=0.3,
        max_tokens=100,
    )
    
    title_lower = title.lower()
    if "reasoning" in title_lower or "the user:" in title_lower or title_lower.startswith("the user"):
        if "제목:" in title:
            parts = title.split("제목:", 1)
            if len(parts) > 1:
                title = parts[1].strip()
        elif title_lower.startswith("the user") or title_lower.startswith("user wants"):
            first_period = title.find(".")
            if first_period > 0:
                title = title[first_period + 1:].strip()
            else:
                first_newline = title.find("\n")
                if first_newline > 0:
                    title = title[first_newline + 1:].strip()
    
    return title


def _extract_title_fallback(summary_md: str, transcript_text: str) -> str:
    """LLM 추출 실패 시 대체 방법으로 제목 생성."""
    lines = summary_md.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title and len(title) <= 512:
                return title
        elif line.startswith("## "):
            title = line[3:].strip()
            if title and len(title) <= 512:
                return title
    
    first_sentence = transcript_text.split(".")[0].strip()
    if first_sentence:
        if len(first_sentence) > 100:
            first_sentence = first_sentence[:97] + "..."
        return first_sentence
    
    return "전문 요약"


def sanitize_summary_output(summary_md: str, transcript_text: str) -> str:
    """LLM 요약 결과에서 프롬프트 잔여물 제거 및 필요시 대체 요약 생성."""
    if not summary_md:
        logger.warning("LLM summary was empty, falling back to heuristic summary.")
        return _build_fallback_summary(transcript_text)
    
    text = summary_md.strip()
    
    text_lower = text.lower()
    if "reasoning" in text_lower and ("the user:" in text_lower or "we need to" in text_lower):
        reasoning_start = text_lower.find("reasoning")
        if reasoning_start > 0:
            text = text[:reasoning_start].strip()
            logger.info("Removed reasoning section from summary output")
    
    lower = text.lower()
    heading_idx = lower.find("## executive summary")
    if heading_idx > 0:
        text = text[heading_idx:]
    
    if _looks_like_prompt_echo(text):
        logger.warning("Detected prompt instructions in summary, generating heuristic summary instead.")
        return _build_fallback_summary(transcript_text)
    
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line_lower = line.lower().strip()
        if any(marker in line_lower for marker in PROMPT_ECHO_MARKERS):
            logger.debug("Removing prompt-like line from summary: %s", line[:100])
            continue
        if "today is thursday" in line_lower or "in rhyme" in line_lower:
            continue
        cleaned_lines.append(line)
    
    text = "\n".join(cleaned_lines).strip()
    
    if _looks_like_prompt_echo(text):
        logger.warning("Summary still contains prompt instructions after cleaning, using fallback.")
        return _build_fallback_summary(transcript_text)
    
    if _is_transcript_echo(text, transcript_text):
        logger.warning("Summary appears to be a transcript echo after cleaning, using fallback.")
        return _build_fallback_summary(transcript_text)
    
    if not _has_korean_characters(text):
        logger.warning("Summary contains no Korean characters, using fallback.")
        return _build_fallback_summary(transcript_text)
    
    return text


