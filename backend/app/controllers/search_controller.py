"""Deep Search API 엔드포인트.

SearXNG 검색 + LiteLLM 요약을 동기적으로 처리합니다.
Phase 1: Backend에서 직접 처리 (Worker 거치지 않음)
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from openai import AsyncOpenAI
from pydantic import BaseModel
from redis.asyncio import Redis

from ..core.config import get_settings
from ..core.logging import logger

router = APIRouter(prefix="/api", tags=["search"])


# ============================================================
# Configuration
# ============================================================

SEARXNG_BASE_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
SEARXNG_TIMEOUT = 30.0

# Valkey 캐싱
SEARCH_CACHE_PREFIX = "search:cache:"
SEARCH_CACHE_TTL = 3600  # 1시간


def get_litellm_base_url() -> str:
    return os.getenv("LITELLM_BASE_URL", "http://litellm:4000")


def get_litellm_api_key() -> str:
    return os.getenv("LITELLM_API_KEY", "")


def get_litellm_model() -> str:
    return os.getenv("LITELLM_MODEL", "qwen3-4b")


@lru_cache(maxsize=1)
def get_async_openai_client() -> AsyncOpenAI:
    """LiteLLM용 AsyncOpenAI 클라이언트."""
    return AsyncOpenAI(
        base_url=get_litellm_base_url(),
        api_key=get_litellm_api_key(),
        timeout=120.0,
    )


async def get_redis_client() -> Redis:
    """Redis/Valkey 클라이언트."""
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


# ============================================================
# Schemas
# ============================================================

class SearchRequest(BaseModel):
    """검색 요청."""
    query: str
    max_results: int = 10
    categories: str = "general"
    language: str = "ko-KR"
    use_cache: bool = True


class SearchSource(BaseModel):
    """검색 소스."""
    position: int
    title: str
    url: str
    snippet: str
    engine: str


class SearchResponse(BaseModel):
    """검색 응답."""
    query: str
    summary: str
    sources: list[SearchSource]
    search_count: int
    citations_used: list[int]
    cached: bool = False
    error: Optional[str] = None


# ============================================================
# RAG Prompt
# ============================================================

SEARCH_SYSTEM_PROMPT = """당신은 웹 검색 결과를 바탕으로 질문에 답변하는 AI 어시스턴트입니다.

규칙:
1. 검색 결과에 있는 정보만 사용하여 답변하세요.
2. 답변에 출처를 반드시 인용하세요. 형식: [1], [2] 등
3. 검색 결과에 없는 정보는 "검색 결과에서 찾을 수 없습니다"라고 명시하세요.
4. 답변은 명확하고 구조화되어야 합니다.
5. 한국어로 답변하세요."""

SEARCH_USER_TEMPLATE = """질문: {query}

검색 결과:
{context}

위 검색 결과를 바탕으로 질문에 답변하세요. 반드시 출처를 [1], [2] 형식으로 인용하세요."""


# ============================================================
# Helper Functions
# ============================================================

def _generate_cache_key(query: str, categories: str, language: str) -> str:
    """캐시 키 생성."""
    content = f"{query}:{categories}:{language}"
    hash_val = hashlib.md5(content.encode()).hexdigest()[:16]
    return f"{SEARCH_CACHE_PREFIX}{hash_val}"


def _format_search_context(results: list[dict]) -> str:
    """검색 결과를 Context 형식으로 포맷팅."""
    if not results:
        return "검색 결과 없음"

    lines = []
    for r in results:
        lines.append(f"[{r['position']}] {r['title']}")
        lines.append(f"URL: {r['url']}")
        lines.append(f"내용: {r['snippet']}")
        lines.append("")

    return "\n".join(lines)


def _extract_citations(text: str) -> list[int]:
    """텍스트에서 Citation 번호 추출."""
    pattern = r"\[(\d+)\]"
    matches = re.findall(pattern, text)
    return sorted(set(int(m) for m in matches))


# ============================================================
# SearXNG Client
# ============================================================

async def search_searxng(
    query: str,
    *,
    limit: int = 10,
    categories: str = "general",
    language: str = "ko-KR",
) -> list[dict]:
    """SearXNG 검색 수행."""
    params = {
        "q": query.strip(),
        "format": "json",
        "categories": categories,
        "language": language,
    }

    url = f"{SEARXNG_BASE_URL}/search"

    logger.info(
        "[Search] SearXNG request: query=%r, categories=%s",
        query[:50], categories
    )

    async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    raw_results = data.get("results", [])
    results = []

    for i, item in enumerate(raw_results[:limit]):
        results.append({
            "position": i + 1,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", "unknown"),
        })

    logger.info(
        "[Search] SearXNG results: count=%d, engines=%s",
        len(results),
        list(set(r["engine"] for r in results))
    )

    return results


# ============================================================
# LLM Summarization
# ============================================================

async def summarize_with_llm(query: str, search_results: list[dict]) -> str:
    """LiteLLM으로 검색 결과 요약."""
    context = _format_search_context(search_results)

    user_message = SEARCH_USER_TEMPLATE.format(
        query=query,
        context=context,
    )

    messages = [
        {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    client = get_async_openai_client()
    model = get_litellm_model()

    logger.info("[Search] LLM request: model=%s, context_length=%d", model, len(context))

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM 응답이 비어있습니다")

    logger.info("[Search] LLM response: length=%d", len(content))

    return content.strip()


# ============================================================
# API Endpoints
# ============================================================

def _detect_category(query: str, default: str = "general") -> str:
    """검색어에서 카테고리를 자동 감지합니다."""
    query_lower = query.lower()

    # 동영상/유튜브 관련 키워드
    video_keywords = ["유튜브", "youtube", "영상", "동영상", "비디오", "video", "채널", "channel"]
    if any(kw in query_lower for kw in video_keywords):
        return "videos"

    # 이미지 관련 키워드
    image_keywords = ["이미지", "사진", "그림", "image", "photo", "picture"]
    if any(kw in query_lower for kw in image_keywords):
        return "images"

    # 뉴스 관련 키워드
    news_keywords = ["뉴스", "news", "기사", "속보", "breaking"]
    if any(kw in query_lower for kw in news_keywords):
        return "news"

    return default


@router.post("/search", response_model=SearchResponse)
async def deep_search(request: SearchRequest):
    """Deep Search API.

    웹 검색 + LLM 요약을 수행하여 출처가 명시된 답변을 반환합니다.
    """
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="검색어가 비어있습니다")

    # 카테고리 자동 감지 (요청에서 명시하지 않은 경우)
    categories = request.categories
    if categories == "general":
        categories = _detect_category(query, "general")
        if categories != "general":
            logger.info("[Search] Auto-detected category: %s", categories)

    # 캐시 확인
    cache_key = _generate_cache_key(query, categories, request.language)

    if request.use_cache:
        try:
            redis = await get_redis_client()
            cached = await redis.get(cache_key)
            if cached:
                logger.info("[Search] Cache hit: query=%r", query[:30])
                result = json.loads(cached)
                result["cached"] = True
                return SearchResponse(**result)
        except Exception as e:
            logger.warning("[Search] Cache read failed: %s", e)

    logger.info("[Search] Starting: query=%r", query[:50])

    try:
        # 1. SearXNG 검색
        search_results = await search_searxng(
            query,
            limit=request.max_results,
            categories=categories,
            language=request.language,
        )

        if not search_results:
            return SearchResponse(
                query=query,
                summary="검색 결과가 없습니다. 다른 검색어를 시도해 보세요.",
                sources=[],
                search_count=0,
                citations_used=[],
            )

        # 2. LLM 요약
        summary = await summarize_with_llm(query, search_results)

        # 3. Citation 추출
        citations = _extract_citations(summary)

        # 4. 결과 구성
        result = {
            "query": query,
            "summary": summary,
            "sources": search_results,
            "search_count": len(search_results),
            "citations_used": citations,
            "cached": False,
        }

        # 5. 캐시 저장
        try:
            redis = await get_redis_client()
            await redis.setex(cache_key, SEARCH_CACHE_TTL, json.dumps(result, ensure_ascii=False))
            logger.info("[Search] Cache stored: query=%r", query[:30])
        except Exception as e:
            logger.warning("[Search] Cache write failed: %s", e)

        logger.info(
            "[Search] Completed: query=%r, sources=%d, citations=%s",
            query[:30], len(search_results), citations
        )

        return SearchResponse(**result)

    except httpx.TimeoutException as e:
        logger.error("[Search] SearXNG timeout: %s", e)
        raise HTTPException(status_code=504, detail="검색 요청 시간 초과")
    except httpx.HTTPStatusError as e:
        logger.error("[Search] SearXNG error: %s", e)
        raise HTTPException(status_code=502, detail=f"검색 서비스 오류: {e.response.status_code}")
    except Exception as e:
        logger.error("[Search] Failed: %s", e)
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")


@router.get("/search", response_model=SearchResponse)
async def deep_search_get(
    q: str = Query(..., description="검색 쿼리"),
    max_results: int = Query(10, ge=1, le=20, description="최대 결과 수"),
    categories: str = Query("general", description="검색 카테고리"),
    language: str = Query("ko-KR", description="검색 언어"),
    use_cache: bool = Query(True, description="캐시 사용 여부"),
):
    """Deep Search API (GET).

    간단한 쿼리 스트링으로 검색할 수 있습니다.
    """
    request = SearchRequest(
        query=q,
        max_results=max_results,
        categories=categories,
        language=language,
        use_cache=use_cache,
    )
    return await deep_search(request)
