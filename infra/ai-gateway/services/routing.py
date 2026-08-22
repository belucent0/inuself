"""Serverless RunPod and Codex provider descriptors."""

from dataclasses import dataclass

from config import (
    CODEX_API_BASE,
    CODEX_MODEL,
    LLM_MODEL_NAME,
    RUNPOD_LLM_BASE_URL,
)

# codex-high/medium/low는 서로 다른 모델이 아니라 동일 모델의 reasoning effort.
# CODEX_MODEL(env)이 실제 모델명의 Single Source of Truth.
CODEX_REASONING_EFFORT_MAP = {
    "codex-high": "high",
    "codex-medium": "medium",
    "codex-low": "low",
}


@dataclass(frozen=True)
class ProviderResult:
    api_base: str
    model: str
    name: str
    reasoning_effort: str | None = None


def get_serverless_llm_provider() -> ProviderResult:
    return ProviderResult(RUNPOD_LLM_BASE_URL, LLM_MODEL_NAME, "runpod-llm")


def get_codex_provider(model_name: str = "codex-medium") -> ProviderResult:
    return ProviderResult(
        CODEX_API_BASE,
        CODEX_MODEL,
        "codex",
        CODEX_REASONING_EFFORT_MAP[model_name],
    )
