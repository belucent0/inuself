"""
검색 품질 평가자

검색 결과의 품질, 개수, 재시도 빈도를 평가
"""

from .base import BaseEvaluator
from ..core.interfaces import ConversationData


class SearchQualityEvaluator(BaseEvaluator):
    """
    검색 품질 평가자

    평가 항목:
    - 검색 결과 품질 점수 (Phase 3 메트릭)
    - 검색 결과 개수
    - 재시도 빈도 (V8.4)
    """

    def __init__(self, threshold: float = 60.0):
        super().__init__(threshold)

    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """검색 품질 평가"""
        assistant_messages = [
            msg for msg in conversation.messages
            if msg.get("role") == "assistant"
        ]

        total_searches = 0
        quality_scores = []
        retry_counts = []
        result_counts = []

        for msg in assistant_messages:
            metadata = msg.get("metadata", {})

            # 검색 쿼리 존재 여부
            search_queries = metadata.get("search_queries", [])
            if not search_queries:
                continue

            total_searches += 1

            # 검색 결과 품질
            search_results = metadata.get("search_results", [])
            result_counts.append(len(search_results))

            if search_results:
                # Quality Score 평균
                avg_quality = sum(
                    r.get("quality_score", 0) for r in search_results
                ) / len(search_results)
                quality_scores.append(avg_quality)

            # 재시도 횟수 (V8.4)
            retry_count = metadata.get("search_retry_count", 0)
            retry_counts.append(retry_count)

        if total_searches == 0:
            return 0.0, {"error": "No search operations"}

        # 메트릭 계산
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
        avg_results = sum(result_counts) / len(result_counts) if result_counts else 0
        avg_retries = sum(retry_counts) / len(retry_counts) if retry_counts else 0

        # 점수 계산
        # - 품질 점수: 60% 가중치
        # - 결과 개수: 30% 가중치 (5개 이상이면 만점)
        # - 재시도 페널티: 재시도 1회당 -5점
        score = avg_quality * 0.6 + min(avg_results / 5 * 100, 100) * 0.3 - avg_retries * 5
        score = max(0, min(100, score))

        metrics = {
            "total_searches": total_searches,
            "avg_quality_score": avg_quality,
            "avg_result_count": avg_results,
            "avg_retry_count": avg_retries,
            "quality_distribution": self._calculate_distribution(quality_scores)
        }

        return score, metrics

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
        issues = []

        # 낮은 검색 품질
        if metrics["avg_quality_score"] < 50:
            issues.append(
                f"낮은 검색 품질: 평균 {metrics['avg_quality_score']:.1f}점"
            )

        # 검색 결과 부족
        if metrics["avg_result_count"] < 3:
            issues.append(
                f"검색 결과 부족: 평균 {metrics['avg_result_count']:.1f}개"
            )

        # 높은 재시도 빈도
        if metrics["avg_retry_count"] > 1:
            issues.append(
                f"높은 재시도 빈도: 평균 {metrics['avg_retry_count']:.1f}회"
            )

        return issues
