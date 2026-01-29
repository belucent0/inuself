"""SearXNG 웹 검색 도구.

LangGraph 노드에서 사용할 웹 검색 함수를 제공합니다.
"""
from __future__ import annotations

import hashlib
import json
from loguru import logger
import os
from typing import Any

import httpx
from redis.asyncio import Redis



# SearXNG 설정
SEARXNG_TIMEOUT = 30.0

# 캐싱 설정
SEARCH_CACHE_PREFIX = "ai:search:cache:"
SEARCH_CACHE_TTL = 3600  # 1시간


class WebSearchError(RuntimeError):
    """웹 검색 실패 예외."""


def _get_searxng_url() -> str:
    """SearXNG URL 반환."""
    return os.getenv("SEARXNG_URL", "http://searxng:8080")


def _generate_cache_key(query: str, categories: str, language: str) -> str:
    """캐시 키 생성."""
    content = f"{query}:{categories}:{language}:v1"
    hash_val = hashlib.md5(content.encode()).hexdigest()[:16]
    return f"{SEARCH_CACHE_PREFIX}{hash_val}"


async def _get_redis_client(settings: Any) -> Redis:
    """Redis 클라이언트 반환."""
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def search_web(
    query: str,
    *,
    settings: Any,
    limit: int = 10,
    categories: str = "general",
    language: str = "ko-KR",
    use_cache: bool = True,
) -> list[dict]:
    """SearXNG를 통한 웹 검색.

    Args:
        query: 검색 쿼리
        settings: 애플리케이션 설정
        limit: 최대 결과 수
        categories: 검색 카테고리 (general, news, images, videos)
        language: 언어 코드
        use_cache: 캐시 사용 여부

    Returns:
        검색 결과 목록

    Raises:
        WebSearchError: 검색 실패
    """
    query = query.strip()
    if not query:
        raise WebSearchError("검색어가 비어있습니다")

    # 카테고리 자동 감지
    if categories == "general":
        categories = _detect_category(query, "general")

    # 캐시 확인
    cache_key = _generate_cache_key(query, categories, language)
    if use_cache:
        try:
            redis = await _get_redis_client(settings)
            cached = await redis.get(cache_key)
            if cached:
                logger.info(f"[WebSearch] Cache hit: {cache_key}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"[WebSearch] Cache read failed: {e}")

    # SearXNG 검색 수행
    base_url = _get_searxng_url()
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": language,
    }

    url = f"{base_url}/search"

    logger.info(f"[WebSearch] Request: query={query[:50]}, categories={categories}")

    try:
        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        raise WebSearchError("검색 시간 초과")
    except httpx.HTTPStatusError as e:
        raise WebSearchError(f"검색 서버 오류: {e.response.status_code}")
    except Exception as e:
        raise WebSearchError(f"검색 실패: {e}")

    raw_results = data.get("results", [])
    results = []

    for i, item in enumerate(raw_results[:limit]):
        results.append({
            "position": i + 1,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", "unknown"),
            "source": "web",
        })

    logger.info(
        f"[WebSearch] Results: count={len(results)}, "
        f"engines={list(set(r['engine'] for r in results))}"
    )

    # 캐시 저장
    if use_cache and results:
        try:
            redis = await _get_redis_client(settings)
            await redis.set(cache_key, json.dumps(results), ex=SEARCH_CACHE_TTL)
            logger.debug(f"[WebSearch] Cache set: {cache_key}")
        except Exception as e:
            logger.warning(f"[WebSearch] Cache write failed: {e}")

    return results


def _detect_category(query: str, default: str = "general") -> str:
    """검색어에서 카테고리 자동 감지.

    Args:
        query: 검색어
        default: 기본 카테고리

    Returns:
        감지된 카테고리
    """
    query_lower = query.lower()

    video_keywords = ["유튜브", "youtube", "영상", "동영상", "비디오", "video", "채널", "channel"]
    if any(kw in query_lower for kw in video_keywords):
        return "videos"

    image_keywords = ["이미지", "사진", "그림", "image", "photo", "picture"]
    if any(kw in query_lower for kw in image_keywords):
        return "images"

    news_keywords = ["뉴스", "news", "기사", "속보", "breaking", "최신"]
    if any(kw in query_lower for kw in news_keywords):
        return "news"

    return default


def format_search_context(results: list[dict]) -> str:
    """검색 결과를 LLM 컨텍스트 형식으로 포맷팅.

    Args:
        results: 검색 결과 목록

    Returns:
        포맷팅된 컨텍스트 문자열
    """
    if not results:
        return "검색 결과 없음"

    lines = []
    for r in results:
        lines.append(f"[{r.get('position', '?')}] {r.get('title', '제목 없음')}")
        lines.append(f"URL: {r.get('url', '')}")
        lines.append(f"내용: {r.get('snippet', '')}")
        lines.append("")

    return "\n".join(lines)
