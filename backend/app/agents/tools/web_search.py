"""SearXNG 웹 검색 도구.

LangGraph 노드에서 사용할 웹 검색 함수를 제공합니다.
"""

from __future__ import annotations

import hashlib
import json
import re
from loguru import logger
import os
from typing import Any
from html import unescape

import httpx
from redis.asyncio import Redis


# SearXNG 설정
SEARXNG_TIMEOUT = 30.0
CONTENT_FETCH_TIMEOUT = 8.0
MAX_FETCH_BYTES = 512_000

# 캐싱 설정
SEARCH_CACHE_PREFIX = "ai:search:cache:"
SEARCH_CACHE_TTL = 3600  # 1시간
CONTENT_CACHE_PREFIX = "ai:search:content:"
CONTENT_CACHE_TTL = 6 * 3600  # 6시간


class WebSearchError(RuntimeError):
    """웹 검색 실패 예외."""


def _get_searxng_url(settings: Any | None = None) -> str:
    """SearXNG URL 반환."""
    configured = getattr(settings, "searxng_url", None) if settings else None
    if configured:
        return configured
    return os.getenv("SEARXNG_URL", "http://searxng:8080")


def _generate_cache_key(
    query: str,
    categories: str,
    language: str,
    time_range: str | None,
) -> str:
    """캐시 키 생성."""
    content = f"{query}:{categories}:{language}:{time_range or 'none'}:v2"
    hash_val = hashlib.md5(content.encode()).hexdigest()[:16]
    return f"{SEARCH_CACHE_PREFIX}{hash_val}"


def _generate_content_cache_key(url: str, max_chars: int) -> str:
    """본문 추출 캐시 키 생성."""
    content = f"{url}:{max_chars}:v1"
    hash_val = hashlib.md5(content.encode()).hexdigest()[:16]
    return f"{CONTENT_CACHE_PREFIX}{hash_val}"


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
    time_range: str | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """SearXNG를 통한 웹 검색.

    Args:
        query: 검색 쿼리
        settings: 애플리케이션 설정
        limit: 최대 결과 수
        categories: 검색 카테고리 (general, news, images, videos)
        language: 언어 코드
        time_range: 기간 필터 (day, week, month, year)
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
    cache_key = _generate_cache_key(query, categories, language, time_range)
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
    base_url = _get_searxng_url(settings)
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": language,
    }

    if time_range in {"day", "week", "month", "year"}:
        params["time_range"] = time_range

    url = f"{base_url}/search"

    logger.info(
        f"[WebSearch] Request: query={query[:50]}, categories={categories}, time_range={time_range or 'none'}"
    )

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
        results.append(
            {
                "position": i + 1,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "engine": item.get("engine", "unknown"),
                "source": "web",
            }
        )

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


def _strip_html_to_text(html: str) -> str:
    """HTML에서 본문 텍스트를 추출한다."""
    cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


async def fetch_url_content(
    url: str,
    *,
    settings: Any,
    max_chars: int = 3000,
    use_cache: bool = True,
) -> dict[str, Any]:
    """URL 본문 텍스트를 가져와 정제한다.

    Args:
        url: 대상 URL
        settings: 애플리케이션 설정
        max_chars: 반환할 최대 문자 수
        use_cache: 캐시 사용 여부

    Returns:
        {"text": str, "length": int}

    Raises:
        WebSearchError: 본문 추출 실패
    """
    target_url = (url or "").strip()
    if not target_url:
        raise WebSearchError("본문 추출 URL이 비어있습니다")

    cache_key = _generate_content_cache_key(target_url, max_chars)
    if use_cache:
        try:
            redis = await _get_redis_client(settings)
            cached = await redis.get(cache_key)
            if cached:
                logger.debug(f"[WebContent] Cache hit: {cache_key}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"[WebContent] Cache read failed: {e}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient(
            timeout=CONTENT_FETCH_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(target_url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise WebSearchError(f"지원하지 않는 콘텐츠 타입: {content_type}")

            text_raw = response.text
            if len(text_raw.encode("utf-8", errors="ignore")) > MAX_FETCH_BYTES:
                text_raw = text_raw[:MAX_FETCH_BYTES]

            if "text/html" in content_type:
                text = _strip_html_to_text(text_raw)
            else:
                text = re.sub(r"\s+", " ", text_raw).strip()

            if not text:
                raise WebSearchError("본문 텍스트 추출 실패")

            payload = {
                "text": text[:max_chars],
                "length": len(text),
            }
    except httpx.TimeoutException:
        raise WebSearchError("본문 추출 시간 초과")
    except httpx.HTTPStatusError as e:
        raise WebSearchError(f"본문 추출 HTTP 오류: {e.response.status_code}")
    except WebSearchError:
        raise
    except Exception as e:
        raise WebSearchError(f"본문 추출 실패: {e}")

    if use_cache:
        try:
            redis = await _get_redis_client(settings)
            await redis.set(cache_key, json.dumps(payload), ex=CONTENT_CACHE_TTL)
        except Exception as e:
            logger.warning(f"[WebContent] Cache write failed: {e}")

    return payload


def _detect_category(query: str, default: str = "general") -> str:
    """검색어에서 카테고리 자동 감지.

    Args:
        query: 검색어
        default: 기본 카테고리

    Returns:
        감지된 카테고리
    """
    query_lower = query.lower()

    video_keywords = [
        "유튜브",
        "youtube",
        "영상",
        "동영상",
        "비디오",
        "video",
        "채널",
        "channel",
    ]
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
