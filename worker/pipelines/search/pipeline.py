"""Deep Search Pipeline - 웹 검색 기반 RAG 요약.

SearXNG로 검색 → LiteLLM으로 요약 → Citation 매핑
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from worker.config import get_settings
from worker.pipelines.llm.litellm_client import (
    request_litellm_completion,
    LiteLLMClientError,
)
from .client import (
    SearXNGClient,
    SearXNGError,
    SearchResult,
    get_searxng_client,
)

logger = logging.getLogger(__name__)


# RAG 프롬프트 템플릿
SEARCH_SUMMARY_SYSTEM_PROMPT = """당신은 웹 검색 결과를 바탕으로 질문에 답변하는 AI 어시스턴트입니다.

규칙:
1. 검색 결과에 있는 정보만 사용하여 답변하세요.
2. 답변에 출처를 반드시 인용하세요. 형식: [1], [2] 등
3. 검색 결과에 없는 정보는 "검색 결과에서 찾을 수 없습니다"라고 명시하세요.
4. 답변은 명확하고 구조화되어야 합니다.
5. 한국어로 답변하세요."""

SEARCH_SUMMARY_USER_TEMPLATE = """질문: {query}

검색 결과:
{context}

위 검색 결과를 바탕으로 질문에 답변하세요. 반드시 출처를 [1], [2] 형식으로 인용하세요."""


@dataclass
class DeepSearchResult:
    """Deep Search 결과."""
    query: str
    summary: str
    sources: list[dict] = field(default_factory=list)
    search_count: int = 0
    citations_used: list[int] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "summary": self.summary,
            "sources": self.sources,
            "search_count": self.search_count,
            "citations_used": self.citations_used,
            "error": self.error,
        }


def format_search_context(results: list[SearchResult]) -> str:
    """검색 결과를 LLM Context 형식으로 포맷팅합니다."""
    if not results:
        return "검색 결과 없음"

    lines = []
    for result in results:
        lines.append(f"[{result.position}] {result.title}")
        lines.append(f"URL: {result.url}")
        lines.append(f"내용: {result.snippet}")
        lines.append("")  # 빈 줄로 구분

    return "\n".join(lines)


def extract_citations(text: str) -> list[int]:
    """텍스트에서 사용된 Citation 번호를 추출합니다."""
    # [1], [2], [3] 등의 패턴 찾기
    pattern = r"\[(\d+)\]"
    matches = re.findall(pattern, text)
    return sorted(set(int(m) for m in matches))


def deep_search(
    query: str,
    *,
    max_results: int = 10,
    categories: str = "general",
    language: str = "ko-KR",
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> DeepSearchResult:
    """Deep Search를 수행합니다.

    1. SearXNG로 웹 검색
    2. 검색 결과를 Context로 구성
    3. LiteLLM으로 RAG 요약
    4. Citation 매핑

    Args:
        query: 검색 질문
        max_results: 최대 검색 결과 수
        categories: 검색 카테고리
        language: 검색 언어
        temperature: LLM temperature
        max_tokens: LLM 최대 토큰

    Returns:
        DeepSearchResult 객체
    """
    settings = get_settings()
    logger.info("Deep Search started: query=%r", query[:50])

    # 1. SearXNG 검색
    try:
        client = get_searxng_client()
        search_results = client.search(
            query,
            limit=max_results,
            categories=categories,
            language=language,
        )
    except SearXNGError as exc:
        logger.error("SearXNG search failed: %s", exc)
        return DeepSearchResult(
            query=query,
            summary="",
            error=f"검색 실패: {exc}",
        )

    if not search_results:
        logger.warning("No search results for query: %r", query)
        return DeepSearchResult(
            query=query,
            summary="검색 결과가 없습니다. 다른 검색어를 시도해 보세요.",
            sources=[],
            search_count=0,
        )

    # 2. Context 구성
    context = format_search_context(search_results)
    logger.debug("Search context: %s", context[:500])

    # 3. LLM 요약
    user_message = SEARCH_SUMMARY_USER_TEMPLATE.format(
        query=query,
        context=context,
    )

    messages = [
        {"role": "system", "content": SEARCH_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        summary = request_litellm_completion(
            settings=settings,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except LiteLLMClientError as exc:
        logger.error("LLM summarization failed: %s", exc)
        return DeepSearchResult(
            query=query,
            summary="",
            sources=[r.to_dict() for r in search_results],
            search_count=len(search_results),
            error=f"요약 생성 실패: {exc}",
        )

    # 4. Citation 추출
    citations_used = extract_citations(summary)
    logger.info(
        "Deep Search completed: query=%r, sources=%d, citations=%s",
        query[:30], len(search_results), citations_used
    )

    return DeepSearchResult(
        query=query,
        summary=summary,
        sources=[r.to_dict() for r in search_results],
        search_count=len(search_results),
        citations_used=citations_used,
    )
