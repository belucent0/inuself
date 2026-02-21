"""Deep Search API 엔드포인트.

SearXNG 검색 + LiteLLM 요약을 스트리밍으로 처리합니다.
SSE(Server-Sent Events)를 사용하여 검색 진행 상황과 결과를 실시간으로 전송합니다.
"""
import hashlib
import json
import os
import re
from functools import lru_cache
from typing import Optional, AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel
from redis.asyncio import Redis

from ..core.config import get_settings
from ..core.logging import logger
from ..core.llm_tier import LLMTier
from ..prompts.search import SEARCH_SYSTEM_PROMPT, SEARCH_USER_TEMPLATE

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


def get_litellm_model(reasoning_mode: bool = False) -> str:
    """사용할 LLM 티어명 조회 (tier 라우팅 사용)."""
    return LLMTier.THINKING if reasoning_mode else LLMTier.SIMPLE


@lru_cache(maxsize=1)
def get_async_openai_client() -> AsyncOpenAI:
    """LiteLLM용 AsyncOpenAI 클라이언트.

    timeout 설정:
    - connect: 연결 수립 30초
    - read: 각 청크 읽기 600초 (추론 모드의 긴 TTFT 대응)
    """
    from httpx import Timeout

    timeout = Timeout(
        connect=30.0,      # 연결 수립
        read=600.0,        # 각 청크 읽기 (TTFT 포함) - 추론 모드 대응
        write=30.0,        # 쓰기
        pool=30.0,         # 풀 연결 대기
    )
    return AsyncOpenAI(
        base_url=get_litellm_base_url(),
        api_key=get_litellm_api_key(),
        timeout=timeout,
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
    reasoning_mode: bool = False


class SearchSource(BaseModel):
    """검색 소스."""
    position: int
    title: str
    url: str
    snippet: str
    engine: str


# ============================================================
# Helper Functions
# ============================================================

def _generate_cache_key(query: str, categories: str, language: str, model: str) -> str:
    """캐시 키 생성."""
    content = f"{query}:{categories}:{language}:{model}:v2"
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
    # 런타임에 URL 가져오기 (환경변수 로딩 순서 문제 방지)
    base_url = os.getenv("SEARXNG_URL", "http://searxng:8080")
    
    params = {
        "q": query.strip(),
        "format": "json",
        "categories": categories,
        "language": language,
    }

    url = f"{base_url}/search"

    logger.info(
        "[Search] SearXNG request: url=%s, query=%r, categories=%s",
        url, query[:50], categories
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
# LLM Streaming Logic
# ============================================================

async def stream_llm_summary(query: str, search_results: list[dict], reasoning_mode: bool = False) -> AsyncGenerator[str, None]:
    """LiteLLM으로 검색 결과 요약 (스트리밍)."""
    import time
    start_time = time.time()

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
    model = get_litellm_model(reasoning_mode)

    # 추론 모드는 <think> 과정이 길어서 더 많은 토큰 필요
    max_tokens = 8192 if reasoning_mode else 2048

    logger.info("[Search] LLM streaming request: model=%s (reasoning=%s, max_tokens=%d)", model, reasoning_mode, max_tokens)

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            stream=True,
        )
    except Exception as e:
        logger.error("[Search] LLM stream creation failed: %s", e)
        raise

    first_token_received = False
    async for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            if not first_token_received:
                ttfb = time.time() - start_time
                logger.info("[Search] First token received (TTFB: %.2fs, reasoning=%s)", ttfb, reasoning_mode)
                first_token_received = True
            yield content


# ============================================================
# API Endpoints
# ============================================================

def _detect_category(query: str, default: str = "general") -> str:
    """검색어에서 카테고리를 자동 감지합니다."""
    query_lower = query.lower()

    video_keywords = ["유튜브", "youtube", "영상", "동영상", "비디오", "video", "채널", "channel"]
    if any(kw in query_lower for kw in video_keywords):
        return "videos"

    image_keywords = ["이미지", "사진", "그림", "image", "photo", "picture"]
    if any(kw in query_lower for kw in image_keywords):
        return "images"

    news_keywords = ["뉴스", "news", "기사", "속보", "breaking"]
    if any(kw in query_lower for kw in news_keywords):
        return "news"

    return default


@router.post("/search")
async def deep_search(request: SearchRequest):
    """Deep Search API (Streaming).

    SSE(Server-Sent Events) 프로토콜을 사용합니다:
    - event: status -> 진행 상태 메시지
    - event: sources -> 검색 결과 (JSON)
    - event: token -> LLM 응답 토큰 (JSON String)
    - event: done -> 완료 신호
    """
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="검색어가 비어있습니다")

    categories = request.categories
    if categories == "general":
        categories = _detect_category(query, "general")

    async def event_generator():
        try:
            # 1. 검색 시작 알림
            yield 'event: status\ndata: "웹 검색을 시작합니다..."\n\n'
            
            # 2. SearXNG 검색
            search_results = await search_searxng(
                query,
                limit=request.max_results,
                categories=categories,
                language=request.language,
            )

            if not search_results:
                yield 'event: status\ndata: "검색 결과가 없습니다."\n\n'
                yield 'event: done\ndata: "[DONE]"\n\n'
                return

            # 3. 소스 전송 (JSON)
            sources_json = json.dumps(search_results, ensure_ascii=False)
            yield f"event: sources\ndata: {sources_json}\n\n"

            # 4. 분석 시작 알림
            yield f'event: status\ndata: "{len(search_results)}개의 문서를 분석하고 있습니다..."\n\n'

            # 5. LLM 스트리밍
            async for token in stream_llm_summary(query, search_results, reasoning_mode=request.reasoning_mode):
                # JSON으로 이스케이프하여 전송 (줄바꿈 등 안전 처리)
                token_json = json.dumps(token, ensure_ascii=False)
                yield f"event: token\ndata: {token_json}\n\n"

            # 6. 완료
            yield 'event: done\ndata: "[DONE]"\n\n'

        except Exception as e:
            logger.error(f"[Search] Streaming error: {e}")
            error_msg = json.dumps(f"오류가 발생했습니다: {str(e)}", ensure_ascii=False)
            yield f"event: error\ndata: {error_msg}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 방지 (필수)
        }
    )


@router.get("/search")
async def deep_search_get(
    q: str = Query(..., description="검색 쿼리"),
    max_results: int = Query(10, ge=1, le=20),
    categories: str = Query("general"),
    language: str = Query("ko-KR"),
):
    """Deep Search API (GET Wrapper)."""
    request = SearchRequest(
        query=q,
        max_results=max_results,
        categories=categories,
        language=language,
    )
    return await deep_search(request)
