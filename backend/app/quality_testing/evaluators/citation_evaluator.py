"""
Citation 유효성 평가자

Citation의 완전성과 본문 내 사용 여부를 평가
"""

from .base import BaseEvaluator
from ..core.interfaces import ConversationData


class CitationEvaluator(BaseEvaluator):
    """
    Citation 유효성 평가자

    평가 항목:
    - Citation 완전성 (URL, title 등 필수 필드)
    - Citation 사용률 (본문에서 [N] 형식으로 참조되는지)
    """

    def __init__(self, threshold: float = 80.0):
        super().__init__(threshold)

    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """Citation 품질 평가"""
        assistant_messages = [
            msg for msg in conversation.messages
            if msg.get("role") == "assistant"
        ]

        total_citations = 0
        valid_citations = 0
        citation_in_text_count = 0

        for msg in assistant_messages:
            metadata = msg.get("metadata", {})
            content = msg.get("content", "")

            citations = metadata.get("citations", [])
            if not citations:
                continue

            total_citations += len(citations)

            # Citation 유효성 검사
            for citation in citations:
                # 필수 필드 존재 여부
                has_url = bool(citation.get("url"))
                has_title = bool(citation.get("title"))

                if has_url and has_title:
                    valid_citations += 1

                # 본문에 Citation 번호 사용 여부
                citation_num = citation.get("citation_number")
                if citation_num and f"[{citation_num}]" in content:
                    citation_in_text_count += 1

        # Citation이 없는 경우 (검색 없는 simple 모드 등)
        if total_citations == 0:
            return 100.0, {"note": "No citations required"}

        # 점수 계산
        validity_rate = (valid_citations / total_citations) * 100
        usage_rate = (citation_in_text_count / total_citations) * 100

        # 가중 평균: 유효성 60%, 사용률 40%
        score = validity_rate * 0.6 + usage_rate * 0.4

        metrics = {
            "total_citations": total_citations,
            "valid_citations": valid_citations,
            "citation_in_text_count": citation_in_text_count,
            "validity_rate": validity_rate,
            "usage_rate": usage_rate
        }

        return score, metrics

    def _find_issues(self, conversation: ConversationData, metrics: dict) -> list[str]:
        """이슈 추출"""
        issues = []

        # 불완전한 Citation
        if "validity_rate" in metrics and metrics["validity_rate"] < 90:
            issues.append(
                f"불완전한 Citation: {metrics['valid_citations']}/{metrics['total_citations']}"
            )

        # Citation 미사용
        if "usage_rate" in metrics and metrics["usage_rate"] < 50:
            issues.append(
                f"Citation 미사용: 본문에서 {metrics['usage_rate']:.1f}%만 참조"
            )

        return issues
