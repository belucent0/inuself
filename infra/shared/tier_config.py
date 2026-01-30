"""LiteLLM 티어 라우팅 설정 - Single Source of Truth.

모든 티어 관련 설정은 이 파일에서만 정의합니다.
custom_handler.py, stream_processor.py 등에서 import하여 사용합니다.
"""
import os
import logging

logger = logging.getLogger(__name__)

# ============================================================
# Tier-based Model Routing (3개 티어)
# ============================================================
# Backend는 "능력 티어"만 결정하고,
# LiteLLM/Provider Manager에서 실제 모델로 변환합니다.
#
# 티어 종류:
# - tier-simple: 간단한 작업 (인사, 짧은 질문)
# - tier-thinking: 복잡한 분석 + Chain-of-Thought 추론
# - tier-summarize: 전사 텍스트 요약 (Transcript 특화 모델)
# ============================================================

TIER_MODEL_MAP = {
    "tier-simple": os.getenv("FLM_LLM_SIMPLE_MODEL", "lfm2:2.6b"),
    "tier-thinking": os.getenv("FLM_THINKING_MODEL", "qwen3-tk:4b"),
    "tier-summarize": os.getenv("FLM_LLM_SUMMARIZE_MODEL", "lfm2-trans:2.6b"),
}


def resolve_tier_to_model(model_name: str) -> str:
    """티어명을 실제 모델명으로 변환.

    Args:
        model_name: 요청된 모델명 (예: "tier-simple", "lfm2:2.6b")

    Returns:
        실제 모델명 (예: "lfm2:2.6b", "lfm2-trans:2.6b")
    """
    if model_name.startswith("tier-"):
        resolved = TIER_MODEL_MAP.get(model_name, TIER_MODEL_MAP.get("tier-simple"))
        logger.info(f"[Tier Routing] {model_name} -> {resolved}")
        return resolved
    return model_name


def get_available_tiers() -> list[str]:
    """사용 가능한 티어 목록 반환."""
    return list(TIER_MODEL_MAP.keys())
