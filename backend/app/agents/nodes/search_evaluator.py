"""Search Quality Evaluator 노드.

V8.4: 검색 결과 품질을 평가하고 재시도 필요 여부를 판단합니다.
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from ..state import GraphState, ThinkingStep


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

        # 재시도 필요 판단
        needs_retry, retry_reason = self._should_retry(
            search_results, total_score, count_score, quality_score, relevance_score
        )

        logger.info(
            f"[SearchEvaluator] Score: {total_score:.1f}/100 "
            f"(count={count_score:.1f}, quality={quality_score:.1f}, relevance={relevance_score:.1f})"
        )

        if needs_retry:
            logger.warning(f"[SearchEvaluator] Retry needed: {retry_reason}")
        else:
            logger.info("[SearchEvaluator] Search quality sufficient, proceeding to generation")

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="search_evaluation",
            content=f"검색 품질 평가: {total_score:.0f}점 ({retry_reason if needs_retry else '충분'})",
            timestamp=time.time()
        ))

        return {
            "search_quality_score": total_score,
            "needs_retry": needs_retry,
            "retry_reason": retry_reason,
            "thinking_steps": thinking_steps,
        }

    def _evaluate_count(self, search_results: list[dict]) -> float:
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

    def _evaluate_quality(self, search_results: list[dict]) -> float:
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
            # quality_score가 없으면 중간 점수 (20점) 부여
            return 20.0

        avg_quality = sum(quality_scores) / len(quality_scores)

        # quality_score (0-100)를 0-40 범위로 변환
        return avg_quality * 0.4

    def _evaluate_relevance(self, search_results: list[dict], keywords: list[str]) -> float:
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
        search_results: list[dict],
        total_score: float,
        count_score: float,
        quality_score: float,
        relevance_score: float,
    ) -> tuple[bool, str]:
        """재시도 필요 여부 판단.

        Args:
            search_results: 검색 결과 목록
            total_score: 총 점수
            count_score: 개수 점수
            quality_score: 품질 점수
            relevance_score: 관련성 점수

        Returns:
            (재시도 필요 여부, 이유)
        """
        # 총점 50점 미만이면 재시도
        if total_score < 50:
            # 구체적 이유 파악
            if len(search_results) == 0:
                return True, "no_results"
            elif len(search_results) < 3:
                return True, "insufficient_results"
            elif quality_score < 20:
                return True, "low_quality"
            elif relevance_score < 10:
                return True, "low_relevance"
            else:
                return True, "overall_low_score"

        # 충분한 품질
        return False, ""
