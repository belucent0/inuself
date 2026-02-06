"""Searcher 노드.

웹 검색을 수행하고 결과를 상태에 추가하는 노드입니다.
Multi-Query 전략과 Reciprocal Rank Fusion(RRF) 리랭킹을 지원합니다.
V8.1: 키워드 기반 관련성 필터링 추가.
V8.3 Phase 3: 신뢰도 평가 및 품질 기반 재정렬 추가.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from loguru import logger

from ..state import GraphState, AIMode, ThinkingStep, SearchResult
from ..tools.web_search import search_web, WebSearchError
from ...utils.quality_assessor import QualityAssessor
from .entity_disambiguator import EntityDisambiguator

# RRF 상수 (일반적으로 60 사용)
RRF_K = 60

# 관련성 필터링 최소 매칭 키워드 수 (2개 이상 매칭되어야 관련성 있음)
MIN_KEYWORD_MATCH = 2


class SearcherNode:
    """웹 검색을 수행하는 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings
        self.quality_assessor = QualityAssessor()
        self.entity_disambiguator = EntityDisambiguator()  # V9.0: Entity Disambiguation

    async def __call__(self, state: GraphState) -> dict:
        """검색 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        original_query = state["query"]
        mode = state["mode"]
        thinking_steps = list(state.get("thinking_steps", []))

        # 재정의된 검색 쿼리 사용 (없으면 원본 쿼리)
        search_queries = state.get("search_queries", [original_query])
        if not search_queries:
            search_queries = [original_query]

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="search_start",
            content=f"웹 검색 시작: {len(search_queries)}개 쿼리",
            timestamp=time.time()
        ))

        logger.info(f"[Searcher] Using reformulated queries: {search_queries}")

        # 검색 옵션 결정 (원본 쿼리로 카테고리 등 결정)
        search_options = self._determine_search_options(original_query, mode)

        # 각 쿼리별 검색 결과 수집 (RRF용 순위 정보 포함)
        # url -> {"result": dict, "ranks": [rank1, rank2, ...]}
        url_to_result: dict[str, dict] = {}
        query_count = 0

        try:
            for sq in search_queries:
                # V9.0 Phase 3: 쿼리 명확화 적용
                clarified_query = self.entity_disambiguator.clarify_query(sq)

                results = await search_web(
                    clarified_query,  # 명확화된 쿼리 사용
                    settings=self.settings,
                    limit=search_options["limit"],
                    categories=search_options["categories"],
                    language=search_options["language"],
                    use_cache=True,
                )

                # 각 결과에 순위 정보 추가
                for rank, r in enumerate(results, start=1):
                    url = r.get("url", "")
                    if not url:
                        continue

                    if url not in url_to_result:
                        url_to_result[url] = {"result": r, "ranks": []}
                    url_to_result[url]["ranks"].append(rank)

                query_count += 1

            # RRF 점수 계산 및 정렬
            scored_results = []
            for url, data in url_to_result.items():
                rrf_score = sum(1.0 / (RRF_K + rank) for rank in data["ranks"])
                scored_results.append((rrf_score, data["result"]))

            # RRF 점수 기준 내림차순 정렬
            scored_results.sort(key=lambda x: x[0], reverse=True)

            # 관련성 필터링 (키워드 기반)
            query_analysis = state.get("query_analysis")
            keywords = query_analysis.get("keywords", []) if query_analysis else []
            logger.info(f"[Searcher] Relevance filter keywords: {keywords}")

            if keywords:
                # 동적 최소 매칭 수: 키워드 3개 이상이면 2개 매칭 필요, 아니면 1개
                min_match = MIN_KEYWORD_MATCH if len(keywords) >= 3 else 1

                filtered_results = []
                filtered_count = 0
                for score, r in scored_results:
                    relevance = self._calculate_relevance(r, keywords)
                    if relevance >= min_match:
                        filtered_results.append((score, r, relevance))
                    else:
                        filtered_count += 1
                        logger.debug(f"[Searcher] Filtered: {r.get('title', '')[:30]}... (relevance={relevance})")

                # 관련성 + RRF 점수로 재정렬 (관련성 우선)
                filtered_results.sort(key=lambda x: (x[2], x[0]), reverse=True)
                results = [r for _, r, _ in filtered_results[:search_options["limit"] * 2]]

                if filtered_count > 0:
                    logger.info(f"[Searcher] Filtered out {filtered_count} irrelevant results (min_match={min_match})")
            else:
                # 키워드 없으면 RRF 점수만 사용
                results = [r for _, r in scored_results[:search_options["limit"] * 2]]

            # [Phase 3] 품질 평가 및 재정렬
            logger.info(f"[Searcher] Found {len(results)} results, now assessing quality...")

            # 각 결과에 품질 점수 부여
            for result in results:
                self.quality_assessor.assess(result)

            # 낮은 품질 결과 필터링 (최소 점수 40)
            results = self.quality_assessor.filter_low_quality(results, min_score=40.0)

            # 품질 점수 기준 재정렬
            results = self.quality_assessor.rerank_by_quality(results)

            # V9.0: Entity Disambiguation 적용
            results = await self.entity_disambiguator.disambiguate(
                query=original_query,
                search_results=results
            )

            # 최종 제한
            results = results[:search_options["limit"]]

            logger.info(f"[Searcher] Final {len(results)} results (quality-filtered, disambiguated, and reranked)")

            # 결과를 SearchResult 형식으로 변환
            search_results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", ""),
                    source="web",
                )
                for r in results
            ]

            thinking_steps.append(ThinkingStep(
                step="search_complete",
                content=f"검색 완료: {len(search_results)}개 결과 발견",
                timestamp=time.time()
            ))

            return {
                "search_results": search_results,
                "thinking_steps": thinking_steps,
            }

        except WebSearchError as e:
            logger.warning(f"[Searcher] Search failed: {e}")
            thinking_steps.append(ThinkingStep(
                step="search_error",
                content=f"검색 실패: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "search_results": [],
                "thinking_steps": thinking_steps,
                "error": f"검색 실패: {e}",
            }

        except Exception as e:
            logger.error(f"[Searcher] Unexpected error: {e}")
            thinking_steps.append(ThinkingStep(
                step="search_error",
                content=f"예상치 못한 오류: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "search_results": [],
                "thinking_steps": thinking_steps,
                "error": f"검색 오류: {e}",
            }

    def _determine_search_options(self, query: str, mode: AIMode) -> dict:
        """쿼리와 모드에 따른 검색 옵션 결정.

        Args:
            query: 검색 쿼리
            mode: AI 모드

        Returns:
            검색 옵션 딕셔너리
        """
        # 기본 옵션
        options = {
            "limit": 10,
            "categories": "general",
            "language": "ko-KR",
        }

        # 모드별 조정
        if mode == AIMode.HYBRID:
            # 하이브리드 모드에서는 더 많은 결과
            options["limit"] = 5  # 웹 검색 5개 + RAG 5개로 분배

        # 쿼리 기반 카테고리 자동 감지
        query_lower = query.lower()

        if any(kw in query_lower for kw in ["뉴스", "news", "최신", "속보"]):
            options["categories"] = "news"
        elif any(kw in query_lower for kw in ["유튜브", "youtube", "영상"]):
            options["categories"] = "videos"
        elif any(kw in query_lower for kw in ["이미지", "사진", "그림"]):
            options["categories"] = "images"

        return options

    def _calculate_relevance(self, result: dict, keywords: list[str]) -> int:
        """검색 결과의 관련성 점수 계산.

        제목과 snippet에 키워드가 몇 개 포함되는지 확인합니다.

        Args:
            result: 검색 결과 딕셔너리
            keywords: 핵심 키워드 목록

        Returns:
            매칭된 키워드 수 (관련성 점수)
        """
        title = result.get("title", "").lower()
        snippet = result.get("snippet", "").lower()
        text = f"{title} {snippet}"

        matched = 0
        for kw in keywords:
            kw_lower = kw.lower()
            # 정규식으로 단어 경계 매칭 (부분 매칭 방지)
            # 한글은 단어 경계가 없으므로 단순 포함 검사
            if re.search(rf'\b{re.escape(kw_lower)}\b', text) or kw_lower in text:
                matched += 1

        return matched
