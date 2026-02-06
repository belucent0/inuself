"""
전체 Quality Score 평가자

Phase 3에서 계산된 search_quality_score를 활용
"""

from .base import BaseEvaluator
from ..core.interfaces import ConversationData


class QualityScoreEvaluator(BaseEvaluator):
    """
    전체 Quality Score 평가자

    Phase 3 메트릭 재사용:
    - Trust Score (신뢰도)
    - Freshness Score (최신성)
    - Content Score (콘텐츠 품질)
    - 가중 평균: Quality = 0.4*Trust + 0.3*Freshness + 0.3*Content
    """

    def __init__(self, threshold: float = 70.0):
        super().__init__(threshold)

    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """전체 품질 점수 평가"""
        assistant_messages = [
            msg for msg in conversation.messages
            if msg.get("role") == "assistant"
        ]

        all_quality_scores = []

        for msg in assistant_messages:
            metadata = msg.get("metadata", {})
            search_quality = metadata.get("search_quality_score")

            if search_quality is not None:
                all_quality_scores.append(search_quality)

        if not all_quality_scores:
            return 0.0, {"error": "No quality scores found"}

        # 평균 품질 점수
        avg_score = sum(all_quality_scores) / len(all_quality_scores)

        metrics = {
            "avg_quality_score": avg_score,
            "min_quality_score": min(all_quality_scores),
            "max_quality_score": max(all_quality_scores),
            "total_evaluated": len(all_quality_scores)
        }

        return avg_score, metrics

    def _find_issues(self, conversation: ConversationData, metrics: dict) -> list[str]:
        """이슈 추출"""
        issues = []

        # 매우 낮은 품질 응답 존재
        if metrics["min_quality_score"] < 40:
            issues.append(
                f"매우 낮은 품질 응답 존재: 최소 {metrics['min_quality_score']:.1f}점"
            )

        # 전체적으로 낮은 품질
        if metrics["avg_quality_score"] < 60:
            issues.append(
                f"전체적으로 낮은 품질: 평균 {metrics['avg_quality_score']:.1f}점"
            )

        return issues
