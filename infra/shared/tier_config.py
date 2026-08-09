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
    "tier-simple": os.getenv("TIER_SIMPLE_MODEL", "gemma4-12b"),
    "tier-thinking": os.getenv("TIER_THINKING_MODEL", "gemma4-12b"),
    "tier-recap": os.getenv("RECAP_SUMMARIZE_MODEL", "gemma4-12b"),
}


def resolve_tier_to_model(model_name: str) -> str:
    """티어명을 실제 모델명으로 변환.

    Args:
        model_name: 요청된 모델명 (예: "tier-simple", "gemma4-12b")

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


# ============================================================
# Tier-based Routing Policy (NPU/GPU 우선순위)
# ============================================================
# v1.2.0 현재: local-gpu 모드에서는 모든 LLM 요청이 vLLM(ai-llm 컨테이너,
# gemma4-12b)으로 단일 라우팅되어 본 정책은 사용되지 않습니다.
# NPU 도입(향후)이나 NPU/GPU 혼합 운영 시 활용하기 위해 정의를 보존합니다.
#
# - primary: 우선 사용할 디바이스 (npu 또는 gpu)
# - fallback: primary busy 시 사용할 대체 디바이스
# - queue_on_busy: 둘 다 busy일 때 대기 여부 (True면 최대 30초 대기)
# ============================================================

TIER_ROUTING_POLICY = {
    "tier-simple": {
        "primary": "npu",
        "fallback": "gpu",
        "queue_on_busy": True,
    },
    "tier-thinking": {
        "primary": "npu",
        "fallback": "gpu",
        "queue_on_busy": True,
    },
    "tier-recap": {
        "primary": "gpu",
        "fallback": "gpu",
        "queue_on_busy": True,
    },
}

# 기본 정책 (tier 정보 없을 때)
DEFAULT_ROUTING_POLICY = {
    "primary": "npu",
    "fallback": "gpu",
    "queue_on_busy": True,
}


def get_routing_policy(tier: str | None) -> dict:
    """티어에 해당하는 라우팅 정책 반환.

    Args:
        tier: 티어명 (예: "tier-simple", "tier-thinking")

    Returns:
        라우팅 정책 딕셔너리 (primary, fallback, queue_on_busy)
    """
    if tier and tier in TIER_ROUTING_POLICY:
        return TIER_ROUTING_POLICY[tier]
    return DEFAULT_ROUTING_POLICY
