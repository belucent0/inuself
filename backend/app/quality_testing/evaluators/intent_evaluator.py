"""
Intent 분석 평가자

Intent 분석의 커버리지와 신뢰도를 평가
"""

from typing import Dict
from .base import BaseEvaluator
from ..core.interfaces import ConversationData


class IntentEvaluator(BaseEvaluator):
    """
    Intent 분석 평가자

    평가 항목:
    - Intent 분석 커버리지 (모든 응답에 Intent 정보가 있는지)
    - Intent 신뢰도 (confidence 평균)
    - 모드 분포 (simple/search/rag/reasoning/hybrid)
    """

    def __init__(self, threshold: float = 70.0):
        super().__init__(threshold)

    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """Intent 분석 품질 평가"""
        assistant_messages = [
            msg for msg in conversation.messages
            if msg.get("role") == "assistant"
        ]

        total_msgs = len(assistant_messages)
        if total_msgs == 0:
            return 0.0, {"error": "No assistant messages"}

        # 메트릭 수집
        has_intent = 0
        total_confidence = 0.0
        mode_distribution: Dict[str, int] = {}

        for msg in assistant_messages:
            metadata = msg.get("metadata", {})
            intent_info = metadata.get("intent")

            if intent_info:
                has_intent += 1
                confidence = intent_info.get("confidence", 0.0)
                total_confidence += confidence

                mode = intent_info.get("mode", "unknown")
                mode_distribution[mode] = mode_distribution.get(mode, 0) + 1

        # 점수 계산
        intent_coverage = (has_intent / total_msgs) * 100
        avg_confidence = (total_confidence / has_intent) if has_intent > 0 else 0.0

        # 가중 평균: 커버리지 70%, 신뢰도 30%
        score = intent_coverage * 0.7 + avg_confidence * 100 * 0.3

        metrics = {
            "total_messages": total_msgs,
            "messages_with_intent": has_intent,
            "intent_coverage": intent_coverage,
            "avg_confidence": avg_confidence,
            "mode_distribution": mode_distribution
        }

        return score, metrics

    def _find_issues(self, conversation: ConversationData, metrics: dict) -> list[str]:
        """이슈 추출"""
        issues = []

        # Intent 분석 누락
        if metrics["intent_coverage"] < 90:
            issues.append(
                f"Intent 분석 누락: {metrics['messages_with_intent']}/{metrics['total_messages']} 메시지"
            )

        # 낮은 Intent 신뢰도
        if metrics["avg_confidence"] < 0.7:
            issues.append(
                f"낮은 Intent 신뢰도: 평균 {metrics['avg_confidence']:.2f}"
            )

        return issues
