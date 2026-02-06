"""
PII Masker 스텁 (Phase 2+)

민감정보 마스킹을 위한 인터페이스 설계
실제 구현은 Phase 2+에서 진행
"""

import re
from typing import Dict

from ..core.interfaces import IMasker, ConversationData


class PIIMasker(IMasker):
    """
    PII(Personally Identifiable Information) 마스킹 (Phase 2+)

    현재는 인터페이스만 구현. 향후 다음 마스킹 적용 예정:
    - 이메일: [EMAIL]
    - 전화번호: [PHONE]
    - 회사명: [COMPANY]
    - 제품명: [PRODUCT]

    Strategy Pattern:
    - 다양한 마스킹 전략을 런타임에 선택 가능
    - 정규식 기반, NER 모델 기반 등 전략 확장 가능
    """

    def __init__(self):
        # Phase 2+에서 구현할 패턴들
        self.patterns = {
            "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "phone": r'\b(?:\+?82-?)?(?:0)?(?:10|11|16|17|18|19)-?\d{3,4}-?\d{4}\b',
            # Phase 2+에서 회사명, 제품명 패턴 추가
        }

        # 원본 → 마스킹 매핑 테이블 (역변환 가능하도록)
        self.masking_map: Dict[str, str] = {}

    def mask_conversation(self, conversation: ConversationData) -> ConversationData:
        """
        대화에서 PII 마스킹 (Phase 2+에서 구현)

        TODO Phase 2+:
        1. 정규식 기반 패턴 매칭
           - 이메일, 전화번호 추출 및 [EMAIL], [PHONE]로 대체
        2. NER 모델 활용
           - 회사명, 제품명 추출 (spaCy, transformers 활용)
           - [COMPANY], [PRODUCT]로 대체
        3. 매핑 테이블 저장
           - 원본 → 마스킹된 값 매핑
           - unmask_if_needed()에서 역변환 가능하도록

        Args:
            conversation: 원본 대화

        Returns:
            마스킹된 대화

        Raises:
            NotImplementedError: Phase 2+에서 구현 예정
        """
        raise NotImplementedError(
            "PIIMasker는 Phase 2+에서 구현 예정입니다. "
            "Phase 1에서는 IdentityMasker를 사용하세요."
        )

    def unmask_if_needed(self, conversation: ConversationData) -> ConversationData:
        """
        마스킹 해제 (Phase 2+에서 구현)

        TODO Phase 2+:
        - masking_map을 사용하여 역변환
        - [EMAIL] → 원본 이메일
        - [COMPANY] → 원본 회사명

        Args:
            conversation: 마스킹된 대화

        Returns:
            원본 대화

        Raises:
            NotImplementedError: Phase 2+에서 구현 예정
        """
        raise NotImplementedError(
            "PIIMasker는 Phase 2+에서 구현 예정입니다."
        )
