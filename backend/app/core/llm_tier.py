"""LLM 티어 상수 정의.

백엔드 레이어에서 사용하는 LLM 능력 티어 상수입니다.
실제 모델 선택은 인프라 레이어(LiteLLM Proxy)에서 담당합니다.

설계 원칙:
- Backend(무엇이 필요한가): tier 상수만 사용
- LiteLLM Proxy(어떻게 라우팅): tier → model 매핑
- Provider Manager(어디서 실행): model → server 매핑
"""

from enum import Enum


class LLMTier(str, Enum):
    """LLM 능력 티어 상수.

    str을 상속하므로 LLMTier.SIMPLE == "tier-simple" 이 성립.
    기존 문자열 비교 코드와 투명하게 호환됩니다.
    """

    SIMPLE = "tier-simple"
    THINKING = "tier-thinking"
    SUMMARIZE = "tier-summarize"


TIER_DISPLAY_MAP: dict[str, str] = {
    LLMTier.SIMPLE: "간단",
    LLMTier.THINKING: "복잡",
    LLMTier.SUMMARIZE: "요약",
}
