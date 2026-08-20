"""AI Gateway 티어 라우팅 설정 - Single Source of Truth.

모든 티어 관련 설정은 이 파일에서만 정의합니다.
backend/worker가 "능력 티어"(tier-simple/tier-thinking/tier-recap)로 요청하면
ai-gateway가 본 매핑을 통해 실제 모델명·추론 컨테이너로 변환합니다.
"""
import os
import logging

logger = logging.getLogger(__name__)

# ============================================================
# Tier-based Model Routing (3개 티어)
# ============================================================
# Backend는 "능력 티어"만 결정하고,
# ai-gateway가 본 매핑을 통해 실제 모델로 변환합니다.
#
# 티어 종류:
# - tier-simple: 간단한 작업 (인사, 짧은 질문)
# - tier-thinking: 복잡한 분석 + Chain-of-Thought 추론
# - tier-recap: 전사 텍스트 요약 (Transcript 특화 모델)
# ============================================================

TIER_MODEL_MAP = {
    "tier-simple": os.getenv("TIER_SIMPLE_MODEL", "gemma4-a4b"),
    "tier-thinking": os.getenv("TIER_THINKING_MODEL", "gemma4-a4b"),
    "tier-recap": os.getenv("RECAP_SUMMARIZE_MODEL", "gemma4-a4b"),
}


def resolve_tier_to_model(model_name: str) -> str:
    """티어명을 실제 모델명으로 변환.

    Args:
        model_name: 요청된 모델명 (예: "tier-simple", "gemma4-a4b")

    Returns:
        실제 모델명
    """
    if model_name.startswith("tier-"):
        resolved = TIER_MODEL_MAP.get(model_name, TIER_MODEL_MAP.get("tier-simple"))
        logger.info(f"[Tier Routing] {model_name} -> {resolved}")
        return resolved
    return model_name


def get_available_tiers() -> list[str]:
    """사용 가능한 티어 목록 반환."""
    return list(TIER_MODEL_MAP.keys())
