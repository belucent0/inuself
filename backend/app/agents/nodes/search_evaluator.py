"""Search Quality Evaluator 노드.

V8.4: 검색 결과 품질을 평가하고 재시도 필요 여부를 판단합니다.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from ..state import GraphState, ThinkingStep, SearchResult

# 하드 게이트 임계치
MIN_RESULTS_HARD_GATE = 3
MIN_AVG_QUALITY_100 = 55.0
MIN_AVG_RELEVANCE_RATIO = 0.45
MIN_TOTAL_SCORE = 55.0
MIN_CONTENT_COVERAGE_RATIO = 0.25


class SearchEvaluatorNode:
    """검색 결과 품질 평가 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """검색 결과 품질 평가.

        평가 기준:
        1. 결과 개수 (40점)
        2. 품질 점수 (40점)
        3. 관련성 (20점)

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        search_results = state.get("search_results", [])
        query = state["query"]
        query_analysis = state.get("query_analysis")
        keywords = query_analysis.get("keywords", []) if query_analysis else []
        thinking_steps = list(state.get("thinking_steps", []))

        logger.info(f"[SearchEvaluator] Evaluating {len(search_results)} results...")

        # 1. 결과 개수 평가 (최대 40점)
        count_score = self._evaluate_count(search_results)

        # 2. 품질 점수 평가 (최대 40점)
        quality_score = self._evaluate_quality(search_results)

        # 3. 관련성 평가 (최대 20점)
        relevance_score = self._evaluate_relevance(search_results, keywords)

        # 총점 계산
        total_score = count_score + quality_score + relevance_score

        content_coverage_ratio = self._evaluate_content_coverage(search_results)

        # 재시도 필요 판단
        needs_retry, retry_reason = self._should_retry(
            search_results,
            total_score,
            count_score,
            quality_score,
            relevance_score,
            keyword_count=len(keywords),
            content_coverage_ratio=content_coverage_ratio,
        )

        logger.info(
            f"[SearchEvaluator] Score: {total_score:.1f}/100 "
            f"(count={count_score:.1f}, quality={quality_score:.1f}, relevance={relevance_score:.1f}, "
            f"content_coverage={content_coverage_ratio:.2f})"
        )

        if needs_retry:
            logger.warning(f"[SearchEvaluator] Retry needed: {retry_reason}")
        else:
            logger.info(
                "[SearchEvaluator] Search quality sufficient, proceeding to generation"
            )

        # 사고 과정 기록
        thinking_steps.append(
            ThinkingStep(
                step="search_evaluation",
                content=f"검색 품질 평가: {total_score:.0f}점 ({retry_reason if needs_retry else '충분'})",
                timestamp=time.time(),
            )
        )

        return {
            "search_quality_score": total_score,
            "needs_retry": needs_retry,
            "retry_reason": retry_reason,
            "thinking_steps": thinking_steps,
        }

    def _evaluate_count(self, search_results: list[SearchResult]) -> float:
        """결과 개수 평가.

        Args:
            search_results: 검색 결과 목록

        Returns:
            점수 (0-40)
        """
        count = len(search_results)

        # 0개: 0점
        # 1개: 10점
        # 2개: 20점
        # 3개: 30점
        # 5개 이상: 40점
        if count == 0:
            return 0.0
        elif count == 1:
            return 10.0
        elif count == 2:
            return 20.0
        elif count == 3:
            return 30.0
        elif count >= 5:
            return 40.0
        else:  # 4개
            return 35.0

    def _evaluate_quality(self, search_results: list[SearchResult]) -> float:
        """품질 점수 평가.

        Args:
            search_results: 검색 결과 목록

        Returns:
            점수 (0-40)
        """
        if not search_results:
            return 0.0

        # 각 결과의 quality_score 평균 (Phase 3에서 이미 계산됨)
        quality_scores = []
        for r in search_results:
            if isinstance(r, dict) and "quality_score" in r:
                quality_scores.append(r["quality_score"])

        if not quality_scores:
            # quality 신호가 누락된 경우 보수적으로 낮은 점수를 부여
            # (재시도 루프가 동작하도록 유도)
            return 10.0

        avg_quality = sum(quality_scores) / len(quality_scores)

        # quality_score (0-100)를 0-40 범위로 변환
        return avg_quality * 0.4

    def _evaluate_relevance(
        self, search_results: list[SearchResult], keywords: list[str]
    ) -> float:
        """관련성 평가 (키워드 매칭).

        Args:
            search_results: 검색 결과 목록
            keywords: 추출된 키워드 목록

        Returns:
            점수 (0-20)
        """
        if not search_results or not keywords:
            return 10.0  # 키워드 없으면 중간 점수

        total_relevance = 0
        for result in search_results:
            if not isinstance(result, dict):
                continue

            precomputed_matches = result.get("relevance_match_count")
            if isinstance(precomputed_matches, (int, float)):
                relevance = min(float(precomputed_matches) / len(keywords), 1.0)
                total_relevance += relevance
                continue

            # 제목 + 스니펫에서 키워드 매칭 수 확인
            title = result.get("title", "").lower()
            snippet = result.get("snippet", "").lower()
            combined_text = f"{title} {snippet}"

            matches = sum(1 for kw in keywords if kw.lower() in combined_text)
            # 키워드의 50% 이상 매칭되면 관련성 높음
            relevance = min(matches / len(keywords), 1.0)
            total_relevance += relevance

        # 평균 관련성을 0-20 범위로 변환
        avg_relevance = total_relevance / len(search_results)
        return avg_relevance * 20

    def _should_retry(
        self,
        search_results: list[SearchResult],
        total_score: float,
        count_score: float,
        quality_score: float,
        relevance_score: float,
        keyword_count: int,
        content_coverage_ratio: float,
    ) -> tuple[bool, str]:
        """재시도 필요 여부 판단.

        Args:
            search_results: 검색 결과 목록
            total_score: 총 점수
            count_score: 개수 점수
            quality_score: 품질 점수
            relevance_score: 관련성 점수
            keyword_count: 키워드 개수
            content_coverage_ratio: 상위 결과 중 본문 확보 비율

        Returns:
            (재시도 필요 여부, 이유)
        """
        result_count = len(search_results)
        avg_quality_100 = quality_score * 2.5  # 0-40 -> 0-100
        avg_relevance_ratio = relevance_score / 20 if keyword_count > 0 else 0.5

        # 1) 하드 게이트: 최소 문서 수
        if result_count == 0:
            return True, "no_results"

        if result_count < MIN_RESULTS_HARD_GATE:
            return True, "insufficient_results"

        # 2) 하드 게이트: 평균 품질
        if avg_quality_100 < MIN_AVG_QUALITY_100:
            return True, "low_quality"

        # 3) 하드 게이트: 평균 관련성 (키워드가 있는 경우만)
        if keyword_count > 0 and avg_relevance_ratio < MIN_AVG_RELEVANCE_RATIO:
            return True, "low_relevance"

        # 4) 본문 커버리지 게이트 (2차 리랭킹 품질)
        has_content_signal = any(
            isinstance(r, dict) and "content_fetch_quality" in r for r in search_results
        )
        if has_content_signal and content_coverage_ratio < MIN_CONTENT_COVERAGE_RATIO:
            return True, "low_content_coverage"

        # 5) 보조 게이트: 종합 점수
        if total_score < MIN_TOTAL_SCORE:
            return True, "overall_low_score"

        # 충분한 품질
        return False, ""

    def _evaluate_content_coverage(self, search_results: list[SearchResult]) -> float:
        """본문 수집 결과의 유효 커버리지를 계산한다 (0-1)."""
        if not search_results:
            return 0.0

        content_enabled_results = [
            r
            for r in search_results
            if isinstance(r, dict)
            and (
                "content_fetch_quality" in r
                or "fetched_content_length" in r
                or "content_preview" in r
            )
        ]
        if not content_enabled_results:
            return 1.0

        good_count = 0
        for result in content_enabled_results:
            length = int(result.get("fetched_content_length", 0))
            quality = float(result.get("content_fetch_quality", 0.0))
            if length >= 400 and quality >= 45.0:
                good_count += 1

        return good_count / len(content_enabled_results)
