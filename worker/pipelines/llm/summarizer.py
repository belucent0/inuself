"""LLM 요약 모듈 - V7.6 마크다운 직접 출력 버전.

LLM이 직접 마크다운 형식으로 요약을 출력합니다.
- JSON 파싱 없이 마크다운 직접 사용
- 제목은 마크다운 첫 줄 '# 제목'에서 추출
- 안정성 향상 (토큰 초과, 파싱 오류 방지)
"""
from __future__ import annotations

import logging
import re

from worker.config import get_settings
from .litellm_client import LiteLLMClientError, request_litellm_completion
from .llamacpp_client import LlamaServerClientError, request_chat_completion

from app.prompts.summary import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    MERGE_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


def _split_text_into_chunks(text: str, max_chars: int = 25000, overlap_chars: int = 1000) -> list[str]:
    """텍스트를 청크로 분할."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        if end < len(text):
            # 문장 경계에서 분할
            for sep in ['. ', '.\n', '! ', '? ', '\n\n']:
                last_sep = text.rfind(sep, start + int(max_chars * 0.7), end)
                if last_sep > start:
                    end = last_sep + len(sep)
                    break

        chunks.append(text[start:end])
        start = end - overlap_chars
        if start < 0:
            start = 0

    return chunks


def _extract_title_from_markdown(markdown: str) -> str:
    """마크다운에서 제목 추출 (# 제목 패턴)."""
    lines = markdown.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('# ') and not line.startswith('## '):
            title = line[2:].strip()
            # 괄호 안의 설명 제거 (예: "# 제목 (설명)" -> "제목")
            title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
            if title and len(title) >= 2:
                return title
    return ""


def _remove_title_from_markdown(markdown: str) -> str:
    """마크다운에서 제목 줄 제거."""
    lines = markdown.strip().split('\n')
    result_lines = []
    title_removed = False

    for line in lines:
        # 첫 번째 # 제목만 제거
        if not title_removed and line.strip().startswith('# ') and not line.strip().startswith('## '):
            title_removed = True
            continue
        result_lines.append(line)

    # 앞쪽 빈 줄 제거
    while result_lines and not result_lines[0].strip():
        result_lines.pop(0)

    return '\n'.join(result_lines)


def _validate_title(title: str, original_text: str) -> bool:
    """제목이 유효한지 검증."""
    if not title or len(title) < 2:
        return False

    # 기본 fallback 제목인지 확인
    if title in ["요약", "전문 요약", "콘텐츠 요약", "제목", "통합 제목"]:
        return False

    # 한글이 하나도 없으면 무효 (한글 요약을 요청했으므로)
    has_korean = any('\uac00' <= char <= '\ud7a3' for char in title)
    if not has_korean:
        logger.info(f"Title has no Korean characters: '{title}'")
        return False

    # 제목이 너무 길면 (50자 초과) 무효
    if len(title) > 50:
        logger.info(f"Title too long ({len(title)} chars): '{title[:30]}...'")
        return False

    # 제목이 원본 텍스트의 일부인지 확인 (20자 이상 매칭)
    title_lower = title.lower()
    text_lower = original_text.lower()[:5000]
    if len(title_lower) >= 20 and title_lower in text_lower:
        logger.info(f"Title is substring of original text: '{title[:30]}...'")
        return False

    return True


def _summarize_single_chunk(
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    settings,
    use_litellm: bool = True
) -> str:
    """단일 청크 요약 - 마크다운 직접 반환."""
    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=chunk)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        if use_litellm:
            response = request_litellm_completion(settings=settings, messages=messages)
        else:
            response = request_chat_completion(settings=settings, messages=messages, stream=False)

        # 마크다운 응답 정리
        markdown = response.strip()

        # ```markdown 블록이 있으면 추출
        md_match = re.search(r'```(?:markdown)?\s*(.*?)\s*```', markdown, re.DOTALL)
        if md_match:
            markdown = md_match.group(1).strip()

        title = _extract_title_from_markdown(markdown)
        logger.info(f"Chunk {chunk_index}/{total_chunks} summarized: title='{title[:30] if title else 'N/A'}...'")
        return markdown

    except (LiteLLMClientError, LlamaServerClientError) as e:
        logger.error(f"Chunk {chunk_index}/{total_chunks} failed: {e}")
        raise RuntimeError(f"요약 실패 (청크 {chunk_index}/{total_chunks}): {e}")


def _merge_summaries(summaries: list[str], settings, use_litellm: bool = True) -> str:
    """여러 청크의 요약을 통합 - 마크다운 직접 반환."""
    if len(summaries) == 1:
        return summaries[0]

    # 부분 요약 텍스트 생성
    parts = []
    for i, md in enumerate(summaries, 1):
        parts.append(f"=== 부분 {i} ===\n{md}")

    summaries_text = "\n\n".join(parts)
    prompt = MERGE_PROMPT_TEMPLATE.format(summaries=summaries_text)

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        if use_litellm:
            response = request_litellm_completion(settings=settings, messages=messages)
        else:
            response = request_chat_completion(settings=settings, messages=messages, stream=False)

        markdown = response.strip()

        # ```markdown 블록이 있으면 추출
        md_match = re.search(r'```(?:markdown)?\s*(.*?)\s*```', markdown, re.DOTALL)
        if md_match:
            markdown = md_match.group(1).strip()

        logger.info(f"Merged {len(summaries)} summaries into final result")
        return markdown

    except Exception as e:
        logger.warning(f"Merge failed, using first chunk: {e}")
        return summaries[0]


def _generate_title_fallback(text: str) -> str:
    """제목 추출 실패 시 대체 방법."""
    # 첫 문장 사용
    first_sentence = text.split('.')[0].strip()
    if first_sentence and len(first_sentence) > 5:
        # 한글이 있는지 확인
        has_korean = any('\uac00' <= char <= '\ud7a3' for char in first_sentence)
        if has_korean:
            if len(first_sentence) > 50:
                return first_sentence[:47] + "..."
            return first_sentence
    return "콘텐츠 요약"


def summarize_text(text: str, on_resource_acquired: callable = None) -> str:
    """전사 텍스트를 요약합니다.

    Args:
        text: 요약할 전사 텍스트
        on_resource_acquired: 리소스 획득 후 콜백 (UI 상태 업데이트용)

    Returns:
        마크다운 형식의 요약
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 텍스트가 비어 있습니다.")

    settings = get_settings()
    use_litellm = settings.llm_provider == "litellm"

    if on_resource_acquired:
        try:
            on_resource_acquired()
        except Exception as e:
            logger.warning(f"on_resource_acquired callback failed: {e}")

    # 청크 분할
    max_chunk_chars = 25000
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)
    logger.info(f"[Summarizer] Text split into {len(chunks)} chunks")

    # 각 청크 요약
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        try:
            result = _summarize_single_chunk(chunk, i, len(chunks), settings, use_litellm)
            summaries.append(result)
        except Exception as e:
            logger.error(f"Chunk {i} failed: {e}")
            continue

    if not summaries:
        raise RuntimeError("모든 청크 요약에 실패했습니다.")

    # 통합
    final_md = _merge_summaries(summaries, settings, use_litellm)

    # 제목 제거 (summarize_text는 제목 없이 반환)
    return _remove_title_from_markdown(final_md)


def summarize_transcription(text: str, on_resource_acquired: callable = None) -> tuple[str, str]:
    """전사 텍스트를 요약하고 제목을 추출합니다.

    Args:
        text: 요약할 전사 텍스트
        on_resource_acquired: 리소스 획득 후 콜백

    Returns:
        (title, summary_md) 튜플
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 텍스트가 비어 있습니다.")

    settings = get_settings()
    use_litellm = settings.llm_provider == "litellm"

    if on_resource_acquired:
        try:
            on_resource_acquired()
        except Exception as e:
            logger.warning(f"on_resource_acquired callback failed: {e}")

    # 청크 분할
    max_chunk_chars = 25000
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)
    logger.info(f"[Summarizer] Text split into {len(chunks)} chunks")

    # 각 청크 요약
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        try:
            result = _summarize_single_chunk(chunk, i, len(chunks), settings, use_litellm)
            summaries.append(result)
        except Exception as e:
            logger.error(f"Chunk {i} failed: {e}")
            continue

    if not summaries:
        raise RuntimeError("모든 청크 요약에 실패했습니다.")

    # 통합
    final_md = _merge_summaries(summaries, settings, use_litellm)

    # 제목 추출
    title = _extract_title_from_markdown(final_md)

    # 제목 검증 - 유효하지 않으면 fallback
    if not _validate_title(title, normalized):
        logger.info(f"[Summarizer] Invalid title: '{title}', using fallback...")
        title = _generate_title_fallback(normalized)

    # 마크다운에서 제목 제거
    summary_md = _remove_title_from_markdown(final_md)

    logger.info(f"[Summarizer] Completed: title='{title}', summary_length={len(summary_md)}")
    return title, summary_md


# ============================================
# 하위 호환성을 위한 함수들
# ============================================

def extract_title(summary_md: str, transcript_text: str) -> str:
    """요약에서 제목 추출 (하위 호환성)."""
    title = _extract_title_from_markdown(summary_md)
    if title:
        return title
    return _generate_title_fallback(transcript_text)


def sanitize_summary_output(summary_md: str, transcript_text: str) -> str:
    """요약 출력 정제 (하위 호환성)."""
    if not summary_md or len(summary_md.strip()) < 50:
        return f"## 요약\n\n{transcript_text[:500]}..."
    return summary_md
