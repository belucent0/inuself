"""
검색 품질 평가자

검색 결과의 품질, 개수, 재시도 빈도를 평가
"""

from collections import Counter
from typing import Any

from .base import BaseEvaluator
from ..core.interfaces import ConversationData


CONTENT_GOOD_LENGTH = 400
CONTENT_GOOD_QUALITY = 45.0


class SearchQualityEvaluator(BaseEvaluator):
    """
    검색 품질 평가자

    평가 항목:
    - 검색 결과 품질 점수 (Phase 3 메트릭)
    - 검색 결과 개수
    - 재시도 빈도 (V8.4)
    - 본문 커버리지/2차 리랭킹 신호 (V10.0 Phase 4)
    """

    def __init__(self, threshold: float = 60.0):
        super().__init__(threshold)

    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """검색 품질 평가"""
        assistant_messages = [
            msg for msg in conversation.messages if msg.get("role") == "assistant"
        ]

        total_searches = 0
        quality_scores = []
        retry_counts = []
        result_counts = []
        content_coverage_ratios = []
        second_stage_scores = []
        retry_reason_counter: Counter[str] = Counter()
        searches_with_content_signal = 0

        for msg in assistant_messages:
            metadata = msg.get("metadata", {})

            # 검색 쿼리 존재 여부
            search_queries = metadata.get("search_queries", [])
            search_results = metadata.get("search_results", [])
            if not search_queries and not search_results:
                continue

            total_searches += 1

            # 검색 결과 품질
            result_counts.append(len(search_results))

            if search_results:
                # Quality Score 평균
                quality_values = [
                    float(r.get("quality_score", 0.0))
                    for r in search_results
                    if isinstance(r, dict)
                    and isinstance(r.get("quality_score"), (int, float))
                ]
                if quality_values:
                    quality_scores.append(sum(quality_values) / len(quality_values))

                second_stage_values = [
                    float(r.get("second_stage_score", 0.0))
                    for r in search_results
                    if isinstance(r, dict)
                    and isinstance(r.get("second_stage_score"), (int, float))
                ]
                if second_stage_values:
                    second_stage_scores.append(
                        sum(second_stage_values) / len(second_stage_values)
                    )

                content_coverage = self._calculate_content_coverage(search_results)
                if content_coverage is not None:
                    searches_with_content_signal += 1
                    content_coverage_ratios.append(content_coverage)

            # 재시도 횟수 (V8.4)
            retry_count = metadata.get("search_retry_count", 0)
            retry_counts.append(retry_count)

            retry_reason = str(metadata.get("retry_reason", "") or "")
            if retry_reason:
                retry_reason_counter[retry_reason] += 1

        if total_searches == 0:
            return 0.0, {"error": "No search operations"}

        # 메트릭 계산
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
        avg_results = sum(result_counts) / len(result_counts) if result_counts else 0
        avg_retries = sum(retry_counts) / len(retry_counts) if retry_counts else 0
        avg_content_coverage = (
            sum(content_coverage_ratios) / len(content_coverage_ratios)
            if content_coverage_ratios
            else 0.5
        )
        avg_second_stage = (
            sum(second_stage_scores) / len(second_stage_scores)
            if second_stage_scores
            else 50.0
        )
        content_enrichment_rate = searches_with_content_signal / total_searches * 100
        retry_reason_distribution = {
            reason: count / total_searches * 100
            for reason, count in retry_reason_counter.items()
        }

        # 점수 계산 (V10.0)
        # - 품질 점수: 45%
        # - 결과 개수: 20% (5개 이상 만점)
        # - 본문 커버리지: 15%
        # - 2차 리랭킹 점수: 10%
        # - 재시도 안정성: 10%
        quality_component = avg_quality * 0.45
        result_component = min(avg_results / 5 * 100, 100) * 0.2
        coverage_component = (avg_content_coverage * 100) * 0.15
        second_stage_component = min(max(avg_second_stage, 0.0), 100.0) * 0.1
        retry_component = max(0.0, 100 - avg_retries * 18) * 0.1

        score = (
            quality_component
            + result_component
            + coverage_component
            + second_stage_component
            + retry_component
        )
        score = max(0, min(100, score))

        metrics = {
            "total_searches": total_searches,
            "avg_quality_score": avg_quality,
            "avg_result_count": avg_results,
            "avg_retry_count": avg_retries,
            "avg_content_coverage_ratio": avg_content_coverage,
            "avg_second_stage_score": avg_second_stage,
            "content_enrichment_rate": content_enrichment_rate,
            "retry_reason_distribution": retry_reason_distribution,
            "content_signal_enabled": searches_with_content_signal > 0,
            "quality_distribution": self._calculate_distribution(quality_scores),
        }

        return score, metrics

    def _calculate_content_coverage(
        self, search_results: list[dict[str, Any]]
    ) -> float | None:
        """상위 결과의 본문 수집 커버리지를 계산한다.

        Returns:
            0~1 커버리지 비율. 본문 신호 자체가 없으면 None.
        """
        content_candidates = [
            r
            for r in search_results
            if isinstance(r, dict)
            and (
                "content_fetch_quality" in r
                or "fetched_content_length" in r
                or "content_preview" in r
            )
        ]
        if not content_candidates:
            return None

        covered = 0
        for result in content_candidates:
            length = int(result.get("fetched_content_length", 0))
            quality = float(result.get("content_fetch_quality", 0.0))
            if length >= CONTENT_GOOD_LENGTH and quality >= CONTENT_GOOD_QUALITY:
                covered += 1

        return covered / len(content_candidates)

    def _calculate_distribution(self, scores: list[float]) -> dict:
        """품질 점수 분포 계산"""
        if not scores:
            return {}

        return {
            "excellent": sum(1 for s in scores if s >= 80) / len(scores) * 100,
            "good": sum(1 for s in scores if 60 <= s < 80) / len(scores) * 100,
            "fair": sum(1 for s in scores if 40 <= s < 60) / len(scores) * 100,
            "poor": sum(1 for s in scores if s < 40) / len(scores) * 100,
        }

    def _find_issues(self, conversation: ConversationData, metrics: dict) -> list[str]:
        """이슈 추출"""
        if "error" in metrics:
            return [str(metrics["error"])]

        issues = []

        # 낮은 검색 품질
        if metrics["avg_quality_score"] < 50:
            issues.append(f"낮은 검색 품질: 평균 {metrics['avg_quality_score']:.1f}점")

        # 검색 결과 부족
        if metrics["avg_result_count"] < 3:
            issues.append(f"검색 결과 부족: 평균 {metrics['avg_result_count']:.1f}개")

        # 높은 재시도 빈도
        if metrics["avg_retry_count"] > 1:
            issues.append(f"높은 재시도 빈도: 평균 {metrics['avg_retry_count']:.1f}회")

        if metrics.get("content_signal_enabled"):
            coverage_ratio = metrics.get("avg_content_coverage_ratio", 0.0)
            if coverage_ratio < 0.35:
                issues.append(
                    f"본문 근거 커버리지 낮음: 평균 {coverage_ratio * 100:.1f}%"
                )

            enrichment_rate = metrics.get("content_enrichment_rate", 0.0)
            if enrichment_rate < 40:
                issues.append(f"본문 수집 적용률 낮음: {enrichment_rate:.1f}%")

        retry_distribution = metrics.get("retry_reason_distribution", {})
        if retry_distribution.get("low_content_coverage", 0.0) >= 30.0:
            issues.append(
                f"재시도 원인 편중: low_content_coverage {retry_distribution['low_content_coverage']:.1f}%"
            )

        return issues
