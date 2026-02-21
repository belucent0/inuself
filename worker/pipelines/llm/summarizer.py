"""LLM 요약 모듈 - 3회 분리 호출 파이프라인.

각 호출의 출력 형식을 단순화하여 파싱 실패를 방지합니다.
- 1단계: 키워드 + 목차 (쉼표 구분 리스트)
- 2단계: 핵심요약 + 상세요약 (마크다운)
- 3단계: 제목 생성
"""
from __future__ import annotations

import logging
import re

from worker.config import get_settings
from .litellm_client import LiteLLMClientError, request_litellm_completion
from .llamacpp_client import LlamaServerClientError, request_chat_completion

from app.prompts.summary import (
    SUMMARY_SYSTEM_PROMPT,
    STEP1_KEYWORDS_TOC_TEMPLATE,
    STEP2_SUMMARY_TEMPLATE,
    STEP3_TITLE_TEMPLATE,
    MERGE_STEP1_TEMPLATE,
    MERGE_STEP2_TEMPLATE,
)

logger = logging.getLogger(__name__)


# ============================================================
# 텍스트 분할
# ============================================================

def _split_text_into_chunks(text: str, max_chars: int = 25000, overlap_chars: int = 1000) -> list[str]:
    """텍스트를 청크로 분할."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        if end < len(text):
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


# ============================================================
# LLM 호출 헬퍼
# ============================================================

def _call_llm(settings, messages: list[dict], use_litellm: bool) -> str:
    """LLM 호출 공통 함수 - 요약 전용 티어(tier-recap) 사용."""
    if use_litellm:
        # 요약 전용 모델 사용: tier-recap → gpt-oss-20b-mxfp4-GGUF
        response = request_litellm_completion(
            settings=settings,
            messages=messages,
            model=settings.litellm_model_summarize,
        )
    else:
        response = request_chat_completion(settings=settings, messages=messages, stream=False)

    result = response.strip()

    # ```markdown 블록 추출
    md_match = re.search(r'```(?:markdown)?\s*(.*?)\s*```', result, re.DOTALL)
    if md_match:
        result = md_match.group(1).strip()

    return result


# ============================================================
# 1단계: 키워드 + 목차 추출
# ============================================================

def _step1_keywords_toc(transcript: str, settings, use_litellm: bool) -> dict:
    """1단계: 키워드 + 목차 추출."""
    prompt = STEP1_KEYWORDS_TOC_TEMPLATE.format(transcript=transcript)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = _call_llm(settings, messages, use_litellm)
    logger.info("[Step1] 키워드+목차 추출 완료: %d chars", len(response))
    logger.info("[Step1] LLM 응답 (앞 300자):\n%s", response[:300])

    # 간단한 파싱: "키워드: ..." 와 "목차: ..." 추출
    result = {"keywords": "", "toc": ""}

    for line in response.split('\n'):
        line = line.strip()
        if line.startswith('키워드:'):
            result["keywords"] = line[4:].strip()
        elif line.startswith('목차:'):
            result["toc"] = line[3:].strip()

    logger.info("[Step1] 파싱 결과: keywords='%s', toc='%s'", result["keywords"][:50] if result["keywords"] else "", result["toc"][:50] if result["toc"] else "")

    # 파싱 실패 시에도 안전하게 처리
    if not result["keywords"]:
        logger.warning("[Step1] 키워드 파싱 실패, fallback 사용. 응답 전체:\n%s", response)
        result["keywords"] = "요약, 분석, 내용"
    if not result["toc"]:
        logger.warning("[Step1] 목차 파싱 실패, fallback 사용")
        result["toc"] = "주요 내용"

    return result


# ============================================================
# 2단계: 핵심요약 + 상세요약
# ============================================================

def _step2_summary(transcript: str, toc: str, settings, use_litellm: bool) -> str:
    """2단계: 핵심요약 + 상세요약."""
    prompt = STEP2_SUMMARY_TEMPLATE.format(toc=toc, transcript=transcript)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = _call_llm(settings, messages, use_litellm)
    logger.info("[Step2] 요약 작성 완료: %d chars", len(response))
    logger.info("[Step2] LLM 응답 (앞 300자):\n%s", response[:300])

    # 후처리: "## 상세 내용" 헤더 보정
    result = response.strip()
    result = _normalize_summary_format(result)

    return result


def _normalize_summary_format(text: str) -> str:
    """요약 마크다운 형식 정규화.

    - LLM이 출력한 "상세 내용" 관련 라벨 모두 제거
    - 첫 ### 앞에 "## 상세 내용" 헤더 추가
    - "소제목1:" 형식을 "### 소제목1" 형식으로 변환
    """
    lines = text.split('\n')
    result_lines = ["## 핵심 요약"]
    detail_header_added = False

    for line in lines:
        stripped = line.strip()

        # LLM이 출력한 "핵심 요약" 라벨 제거
        if stripped in ('핵심 요약', '## 핵심 요약', '## 핵심요약', '핵심요약'):
            continue
        if stripped.startswith('## 핵심'):
            continue

        # LLM이 출력한 "상세 내용" 라벨은 모두 제거 (후처리에서 일관되게 추가)
        if stripped in ('상세 내용', '## 상세 내용', '## 상세내용', '상세내용'):
            continue
        if stripped.startswith('## 상세'):
            continue

        # 첫 ### 헤더 앞에 "## 상세 내용" 추가
        if stripped.startswith('###') and not detail_header_added:
            result_lines.append('\n## 상세 내용')
            detail_header_added = True

        # "소제목1: " 형식을 "### 소제목1" 형식으로 변환
        if re.match(r'^소제목\d+:', stripped):
            if not detail_header_added:
                result_lines.append('\n## 상세 내용')
                detail_header_added = True
            title = stripped.split(':', 1)[1].strip() if ':' in stripped else stripped
            result_lines.append(f'### {title}')
            continue

        result_lines.append(line)

    return '\n'.join(result_lines)


# ============================================================
# 3단계: 제목 생성
# ============================================================

def _step3_title(summary: str, settings, use_litellm: bool) -> str:
    """3단계: 제목 생성."""
    # 요약이 너무 길면 앞부분만 사용
    prompt = STEP3_TITLE_TEMPLATE.format(summary=summary[:3000])
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = _call_llm(settings, messages, use_litellm)

    # "제목:" 접두사 제거
    title = response.strip()
    if title.startswith('제목:'):
        title = title[3:].strip()

    # 첫 줄만 사용
    title = title.split('\n')[0].strip()

    # # 마크다운 헤더 제거
    if title.startswith('#'):
        title = title.lstrip('#').strip()

    logger.info("[Step3] 제목 생성 완료: '%s'", title[:30])

    return title


# ============================================================
# 마크다운 조합
# ============================================================

def _compose_final_markdown(keywords: str, toc: str, summary: str) -> str:
    """최종 마크다운 조합."""
    parts = []

    # 키워드 (그대로)
    if keywords:
        parts.append(f"## 키워드\n{keywords}")

    # 목차 (쉼표 구분을 bullet 리스트로 변환)
    if toc:
        toc_items = [item.strip() for item in toc.split(',') if item.strip()]
        if toc_items:
            toc_md = '\n'.join(f"- {item}" for item in toc_items)
            parts.append(f"## 목차\n{toc_md}")

    # 요약 (마크다운 그대로)
    if summary:
        parts.append(summary)

    return '\n\n'.join(parts)


# ============================================================
# 청크 병합 (긴 텍스트용)
# ============================================================

def _merge_step1_results(results: list[dict], settings, use_litellm: bool) -> dict:
    """여러 청크의 1단계 결과를 통합."""
    if len(results) == 1:
        return results[0]

    # 각 결과를 텍스트로
    parts_list = []
    for i, r in enumerate(results, 1):
        parts_list.append(f"=== 부분 {i} ===\n키워드: {r['keywords']}\n목차: {r['toc']}")

    parts_text = "\n\n".join(parts_list)
    prompt = MERGE_STEP1_TEMPLATE.format(parts=parts_text)

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = _call_llm(settings, messages, use_litellm)
    logger.info("[Merge Step1] %d개 청크 통합 완료", len(results))

    # 파싱
    result = {"keywords": "", "toc": ""}

    for line in response.split('\n'):
        line = line.strip()
        if line.startswith('키워드:'):
            result["keywords"] = line[4:].strip()
        elif line.startswith('목차:'):
            result["toc"] = line[3:].strip()

    # 파싱 실패 시 fallback
    if not result["keywords"]:
        result["keywords"] = results[0]["keywords"]
    if not result["toc"]:
        result["toc"] = results[0]["toc"]

    return result


def _merge_step2_summaries(summaries: list[str], settings, use_litellm: bool) -> str:
    """여러 청크의 2단계 요약을 통합."""
    if len(summaries) == 1:
        return summaries[0]

    # 각 요약을 텍스트로
    parts = []
    for i, s in enumerate(summaries, 1):
        parts.append(f"=== 부분 {i} ===\n{s}")

    summaries_text = "\n\n".join(parts)
    prompt = MERGE_STEP2_TEMPLATE.format(summaries=summaries_text)

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = _call_llm(settings, messages, use_litellm)
    logger.info("[Merge Step2] %d개 요약 통합 완료", len(summaries))

    return response.strip()


# ============================================================
# 제목 검증
# ============================================================

def _validate_title(title: str, original_text: str) -> bool:
    """제목이 유효한지 검증."""
    if not title or len(title) < 2:
        return False

    # 시스템 프롬프트 출력 감지
    invalid_titles = [
        "요약", "전문 요약", "콘텐츠 요약", "제목", "통합 제목",
        "콘텐츠 요약 전문가", "콘텐츠 요약 전문가입니다",
    ]
    if title in invalid_titles or "전문가입니다" in title:
        logger.warning("[Validate] 시스템 프롬프트 출력 감지: '%s'", title)
        return False

    has_korean = any('\uac00' <= char <= '\ud7a3' for char in title)
    if not has_korean:
        logger.info("Title has no Korean characters: '%s'", title)
        return False

    if len(title) > 50:
        logger.info("Title too long (%d chars): '%s...'", len(title), title[:30])
        return False

    return True


def _generate_title_fallback(text: str) -> str:
    """제목 추출 실패 시 대체 방법."""
    first_sentence = text.split('.')[0].strip()
    if first_sentence and len(first_sentence) > 5:
        has_korean = any('\uac00' <= char <= '\ud7a3' for char in first_sentence)
        if has_korean:
            if len(first_sentence) > 50:
                return first_sentence[:47] + "..."
            return first_sentence
    return "콘텐츠 요약"


# ============================================================
# 메인 함수
# ============================================================

def summarize_transcription(text: str) -> tuple[str, str]:
    """전사 텍스트를 3회 분리 호출 파이프라인으로 요약합니다.

    Args:
        text: 요약할 전사 텍스트

    Returns:
        (title, summary_md) 튜플
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 텍스트가 비어 있습니다.")

    settings = get_settings()
    use_litellm = settings.llm_provider == "litellm"

    # 청크 분할
    max_chunk_chars = 25000
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)
    logger.info("[Summarizer] 텍스트를 %d개 청크로 분할", len(chunks))

    try:
        # ========================================
        # 1단계: 키워드 + 목차 추출
        # ========================================
        step1_results = []
        for i, chunk in enumerate(chunks, 1):
            logger.info("[Step1] 청크 %d/%d - 키워드+목차 추출", i, len(chunks))
            result = _step1_keywords_toc(chunk, settings, use_litellm)
            step1_results.append(result)

        # 청크 통합 (여러 개인 경우)
        if len(step1_results) > 1:
            merged_step1 = _merge_step1_results(step1_results, settings, use_litellm)
        else:
            merged_step1 = step1_results[0]

        keywords = merged_step1["keywords"]
        toc = merged_step1["toc"]

        # ========================================
        # 2단계: 핵심요약 + 상세요약
        # ========================================
        summaries = []
        for i, chunk in enumerate(chunks, 1):
            logger.info("[Step2] 청크 %d/%d - 요약 작성", i, len(chunks))
            summary = _step2_summary(chunk, toc, settings, use_litellm)
            summaries.append(summary)

        if len(summaries) > 1:
            merged_summary = _merge_step2_summaries(summaries, settings, use_litellm)
        else:
            merged_summary = summaries[0]

        # ========================================
        # 3단계: 제목 생성
        # ========================================
        logger.info("[Step3] 제목 생성")
        title = _step3_title(merged_summary, settings, use_litellm)

        # 제목 검증
        if not _validate_title(title, normalized):
            logger.warning("[Step3] 제목 검증 실패, fallback: '%s'", title)
            title = _generate_title_fallback(normalized)

        # ========================================
        # 최종 마크다운 조합
        # ========================================
        final_md = _compose_final_markdown(keywords, toc, merged_summary)

        logger.info("[Summarizer] 완료: title='%s', len=%d", title, len(final_md))
        return title, final_md

    except (LiteLLMClientError, LlamaServerClientError) as e:
        logger.error("[Summarizer] LLM 호출 실패: %s", e)
        raise RuntimeError(f"요약 실패: {e}") from e


def summarize_text(text: str) -> str:
    """전사 텍스트를 요약합니다 (제목 없이 반환).

    Args:
        text: 요약할 전사 텍스트

    Returns:
        마크다운 형식의 요약
    """
    _, summary_md = summarize_transcription(text)
    return summary_md
