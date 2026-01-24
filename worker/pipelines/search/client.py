"""SearXNG API 클라이언트.

Docker 내부망에서 SearXNG 메타 검색 엔진에 접근합니다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Docker 내부망 URL
SEARXNG_BASE_URL = "http://searxng:8080"
DEFAULT_TIMEOUT = 30.0


class SearXNGError(RuntimeError):
    """SearXNG API 호출 실패 시 사용하는 예외."""


@dataclass
class SearchResult:
    """개별 검색 결과."""
    title: str
    url: str
    snippet: str
    engine: str
    position: int

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "position": self.position,
        }


class SearXNGClient:
    """SearXNG 검색 클라이언트."""

    def __init__(
        self,
        base_url: str = SEARXNG_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        categories: str = "general",
        language: str = "ko-KR",
    ) -> list[SearchResult]:
        """웹 검색을 수행하고 결과를 반환합니다.

        Args:
            query: 검색 쿼리
            limit: 최대 결과 수
            categories: 검색 카테고리 (general, images, news, etc.)
            language: 검색 언어

        Returns:
            검색 결과 리스트

        Raises:
            SearXNGError: API 호출 실패 시
        """
        if not query or not query.strip():
            raise SearXNGError("검색 쿼리가 비어있습니다.")

        params = {
            "q": query.strip(),
            "format": "json",
            "categories": categories,
            "language": language,
        }

        url = f"{self.base_url}/search"

        logger.info(
            "SearXNG search: query=%r, categories=%s, language=%s",
            query[:50], categories, language
        )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            error_msg = f"SearXNG 요청 타임아웃: {exc}"
            logger.error(error_msg)
            raise SearXNGError(error_msg) from exc
        except httpx.HTTPStatusError as exc:
            error_msg = f"SearXNG HTTP 에러 ({exc.response.status_code}): {exc}"
            logger.error(error_msg)
            raise SearXNGError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"SearXNG 연결 실패: {exc}"
            logger.error(error_msg)
            raise SearXNGError(error_msg) from exc
        except Exception as exc:
            error_msg = f"SearXNG 요청 실패: {exc}"
            logger.error(error_msg)
            raise SearXNGError(error_msg) from exc

        # 결과 파싱
        raw_results = data.get("results", [])
        results: list[SearchResult] = []

        for i, item in enumerate(raw_results[:limit]):
            result = SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),  # SearXNG은 'content' 필드 사용
                engine=item.get("engine", "unknown"),
                position=i + 1,
            )
            results.append(result)

        logger.info(
            "SearXNG results: query=%r, count=%d, engines=%s",
            query[:30],
            len(results),
            list(set(r.engine for r in results))
        )

        return results


# 싱글톤 클라이언트
_client: Optional[SearXNGClient] = None


def get_searxng_client() -> SearXNGClient:
    """SearXNG 클라이언트 싱글톤을 반환합니다."""
    global _client
    if _client is None:
        _client = SearXNGClient()
    return _client
