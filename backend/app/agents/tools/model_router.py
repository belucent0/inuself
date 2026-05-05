"""Tier 기반 라우터.

쿼리의 모드·길이·키워드로 적절한 "능력 티어"를 결정합니다. 실제 모델 매핑은
`infra/shared/tier_config.py` 의 `TIER_MODEL_MAP`이 담당합니다.

설계 원칙:
- Backend(LangGraph): WHAT — "이 쿼리에 어떤 능력이 필요한가?" (tier 결정)
- Infrastructure(ai-gateway): HOW — "그 능력을 어떤 모델로 제공할 것인가?" (model 결정)
"""
from __future__ import annotations

import json
from loguru import logger
from pathlib import Path
from typing import Any
from functools import lru_cache

from ...core.llm_tier import LLMTier



# 라우팅 규칙 파일 경로
ROUTING_RULES_PATH = Path(__file__).parent.parent / "routing_rules.json"


@lru_cache(maxsize=1)
def _load_routing_rules() -> dict:
    """라우팅 규칙 로드 (캐싱)."""
    try:
        with open(ROUTING_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[TierRouter] Failed to load routing rules: {e}")
        return {"default_tier": LLMTier.SIMPLE, "rules": []}


class TierRouter:
    """규칙 기반 Tier 라우터.

    쿼리의 모드·길이·키워드로 적합한 능력 티어를 결정합니다.

    Tiers:
    - tier-simple: 간단한 작업 (인사, 짧은 질문)
    - tier-thinking: 복잡한 분석 + Chain-of-Thought 추론
    """

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings
        self.rules = _load_routing_rules()
        self.default_tier = self.rules.get("default_tier", LLMTier.SIMPLE)

    async def select_tier(self, query: str, mode: str = None, context_size: int = 0) -> str:
        """쿼리에 적합한 능력 티어 선택.

        Args:
            query: 사용자 쿼리
            mode: AI 모드 (참고용)
            context_size: 컨텍스트 크기 (토큰 수 추정)

        Returns:
            선택된 티어명 (tier-simple, tier-thinking)
        """
        # 1. 모드 기반 빠른 판단 (REASONING은 항상 tier-thinking)
        if mode == "reasoning":
            logger.info(f"[TierRouter] REASONING mode -> tier-thinking")
            return LLMTier.THINKING

        # 2. 컨텍스트 크기 기반 판단 (많은 문서 = 복잡한 작업)
        if context_size > 3000:
            logger.info(f"[TierRouter] Large context ({context_size}) -> tier-thinking")
            return LLMTier.THINKING

        # 3. 규칙 기반 라우팅
        return self._rule_based_routing(query)

    def _rule_based_routing(self, query: str) -> str:
        """규칙 기반 라우팅 (폴백).

        Args:
            query: 사용자 쿼리

        Returns:
            선택된 티어명
        """
        query_lower = query.lower()

        # 복잡한 분석이 필요한 키워드
        complex_keywords = [
            "분석", "비교", "왜", "어떻게", "설명", "단계별",
            "차이점", "장단점", "원인", "이유", "전략", "계획",
            "추론", "판단", "평가", "예측"
        ]

        if any(kw in query_lower for kw in complex_keywords):
            logger.info(f"[TierRouter] Rule-based: complex keywords detected -> tier-thinking")
            return LLMTier.THINKING

        # 긴 질문은 복잡한 것으로 간주
        if len(query) > 100:
            logger.info(f"[TierRouter] Rule-based: long query ({len(query)} chars) -> tier-thinking")
            return LLMTier.THINKING

        logger.info(f"[TierRouter] Rule-based: default -> {self.default_tier}")
        return self.default_tier


# 하위 호환성을 위한 별칭 (기존 코드에서 ModelRouter로 import하는 경우)
ModelRouter = TierRouter
