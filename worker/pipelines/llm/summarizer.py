"""LLM 요약 모듈 - V6.6 간소화 버전.

효율적인 프롬프트로 제목, 핵심 요약, 키워드, 목차를 추출합니다.
- 단일 프롬프트 (문서 타입 분류 제거)
- JSON 응답으로 제목 포함 (별도 LLM 호출 제거)
- 토큰 사용량 최적화
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from worker.config import get_settings
from .litellm_client import LiteLLMClientError, request_litellm_completion
from .llamacpp_client import LlamaServerClientError, request_chat_completion

# backend/app/prompts 에서 공유 프롬프트 import (docker-compose에서 app 마운트됨)
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


def _parse_json_response(response: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 파싱."""
    # JSON 블록 추출 시도
    json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 직접 JSON 파싱 시도
        json_str = response.strip()
        # { 로 시작하는 부분 찾기
        start_idx = json_str.find('{')
        if start_idx >= 0:
            json_str = json_str[start_idx:]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 텍스트에서 필드 추출 시도
        logger.warning("JSON parsing failed, extracting fields from text")
        return _extract_fields_from_text(response)


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    """텍스트에서 필드 추출 (JSON 파싱 실패 시 fallback).

    주의: raw text를 그대로 summary로 사용하지 않음.
    각 필드를 regex로 추출하고, summary 필드가 없으면 에러 발생.
    """
    result = {
        "title": "",
        "toc": [],
        "summary": "",
        "keywords": []
    }

    # 제목 추출
    title_match = re.search(r'"title"\s*:\s*"([^"]+)"', text)
    if title_match:
        result["title"] = title_match.group(1)

    # summary 필드 추출 (핵심!)
    # 다양한 형식 처리: "summary": "..." 또는 "summary": "..." (escape된 경우)
    summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if summary_match:
        # escape된 문자 처리
        summary_text = summary_match.group(1)
        summary_text = summary_text.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        result["summary"] = summary_text
    else:
        # summary 필드가 없는 경우, 텍스트가 JSON이 아닌 순수 응답인지 확인
        if not text.strip().startswith('{') and len(text.strip()) > 50:
            # JSON이 아닌 순수 텍스트 응답은 그대로 사용 가능
            result["summary"] = text.strip()
        else:
            # JSON 형식이지만 summary를 추출할 수 없는 경우 - 에러 발생
            raise ValueError(f"LLM 응답에서 summary 필드를 추출할 수 없습니다: {text[:200]}...")

    # 키워드 추출
    keywords_match = re.search(r'"keywords"\s*:\s*\[([^\]]+)\]', text)
    if keywords_match:
        keywords_str = keywords_match.group(1)
        result["keywords"] = [k.strip().strip('"\'') for k in keywords_str.split(',')]

    # toc 추출 (배열 형식)
    toc_match = re.search(r'"toc"\s*:\s*\[((?:[^\[\]]|\[(?:[^\[\]])*\])*)\]', text)
    if toc_match:
        toc_str = toc_match.group(1)
        toc_items = re.findall(r'"([^"]+)"', toc_str)
        result["toc"] = toc_items

    return result


def _summarize_single_chunk(
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    settings,
    use_litellm: bool = True
) -> dict[str, Any]:
    """단일 청크 요약."""
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

        result = _parse_json_response(response)
        logger.info(f"Chunk {chunk_index}/{total_chunks} summarized: title='{result.get('title', '')[:30]}...'")
        return result

    except (LiteLLMClientError, LlamaServerClientError) as e:
        logger.error(f"Chunk {chunk_index}/{total_chunks} failed: {e}")
        raise RuntimeError(f"요약 실패 (청크 {chunk_index}/{total_chunks}): {e}")


def _merge_summaries(summaries: list[dict[str, Any]], settings, use_litellm: bool = True) -> dict[str, Any]:
    """여러 청크의 요약을 통합."""
    if len(summaries) == 1:
        return summaries[0]

    # 부분 요약 텍스트 생성
    parts = []
    for i, s in enumerate(summaries, 1):
        parts.append(f"=== 부분 {i} ===\n제목: {s.get('title', '')}\n요약:\n{s.get('summary', '')}")

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

        result = _parse_json_response(response)

        # 키워드 통합 (모든 청크에서 수집)
        all_keywords = set()
        for s in summaries:
            all_keywords.update(s.get('keywords', []))
        if not result.get('keywords'):
            result['keywords'] = list(all_keywords)[:10]

        logger.info(f"Merged {len(summaries)} summaries into final result")
        return result

    except Exception as e:
        logger.warning(f"Merge failed, using first chunk: {e}")
        return summaries[0]


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
    max_chunk_chars = 25000  # ~10000 토큰
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
    final = _merge_summaries(summaries, settings, use_litellm)

    # 마크다운 형식으로 변환
    return _format_as_markdown(final)


def _format_as_markdown(data: dict[str, Any]) -> str:
    """JSON 데이터를 마크다운으로 변환."""
    parts = []

    # 제목
    title = data.get('title', '요약')
    parts.append(f"# {title}\n")

    # 목차
    toc = data.get('toc', [])
    if toc:
        parts.append("## 목차")
        for item in toc:
            parts.append(f"- {item}")
        parts.append("")

    # 핵심 요약
    summary = data.get('summary', '')
    if summary:
        # summary가 이미 마크다운 형식이면 그대로 사용
        if summary.startswith('#'):
            parts.append(summary)
        else:
            parts.append("## 핵심 요약")
            parts.append(summary)
        parts.append("")

    # 키워드
    keywords = data.get('keywords', [])
    if keywords:
        parts.append("## 키워드")
        parts.append(", ".join(keywords))
        parts.append("")

    return "\n".join(parts)


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
    final = _merge_summaries(summaries, settings, use_litellm)

    # 제목과 마크다운 반환
    title = final.get('title', '요약')
    if not title or len(title) < 2:
        title = _extract_title_fallback(normalized)

    summary_md = _format_as_markdown(final)

    logger.info(f"[Summarizer] Completed: title='{title}', summary_length={len(summary_md)}")
    return title, summary_md


def _extract_title_fallback(text: str) -> str:
    """제목 추출 실패 시 대체 방법."""
    # 첫 문장 사용
    first_sentence = text.split('.')[0].strip()
    if first_sentence and len(first_sentence) > 5:
        if len(first_sentence) > 50:
            return first_sentence[:47] + "..."
        return first_sentence
    return "전문 요약"


# ============================================
# 하위 호환성을 위한 함수들
# ============================================

def extract_title(summary_md: str, transcript_text: str) -> str:
    """요약에서 제목 추출 (하위 호환성)."""
    # 첫 번째 # 제목 찾기
    lines = summary_md.split('\n')
    for line in lines:
        if line.startswith('# '):
            return line[2:].strip()
    return _extract_title_fallback(transcript_text)


def sanitize_summary_output(summary_md: str, transcript_text: str) -> str:
    """요약 출력 정제 (하위 호환성)."""
    if not summary_md or len(summary_md.strip()) < 50:
        return f"## 요약\n\n{transcript_text[:500]}..."
    return summary_md
