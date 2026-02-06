"""
Identity Masker (Phase 1)

마스킹하지 않는 기본 구현체
Phase 2+에서 실제 마스킹 구현체로 교체 가능
"""

from ..core.interfaces import IMasker, ConversationData


class IdentityMasker(IMasker):
    """
    마스킹하지 않는 기본 Masker (Phase 1)

    Strategy Pattern의 기본 전략:
    - Phase 1에서는 마스킹 없이 원본 대화 그대로 반환
    - Phase 2+에서 PIIMasker로 교체하여 민감정보 마스킹 적용 가능
    - Liskov Substitution Principle: IMasker를 구현하므로 투명하게 교체 가능
    """

    def mask_conversation(self, conversation: ConversationData) -> ConversationData:
        """
        마스킹 없이 원본 반환

        Args:
            conversation: 원본 대화

        Returns:
            원본 대화 (변경 없음)
        """
        return conversation

    def unmask_if_needed(self, conversation: ConversationData) -> ConversationData:
        """
        마스킹 안 했으므로 원본 반환

        Args:
            conversation: 대화

        Returns:
            원본 대화 (변경 없음)
        """
        return conversation
