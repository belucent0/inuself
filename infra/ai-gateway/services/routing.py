"""Provider 라우팅 — Serverless(RunPod) + Codex 전용.

local-gpu 모드의 chat/OCR/ASR/Diarize는 ai-gateway가 추론 컨테이너를
직접 httpx로 호출하므로 routing 불필요(routes/chat.py, routes/media.py가
직결). 본 모듈은 serverless(RunPod) 모드와 Codex 라우팅만 담당.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import (
    CODEX_API_BASE,
    CODEX_MODEL,
    RUNPOD_ASR_BASE_URL,
    RUNPOD_EMBED_BASE_URL,
    RUNPOD_LLM_BASE_URL,
    RUNPOD_VISION_BASE_URL,
    resolve_tier_to_model,
)

logger = logging.getLogger(__name__)

# codex-high/medium/low는 서로 다른 모델이 아니라 동일 모델의 reasoning effort.
# CODEX_MODEL(env)이 실제 모델명의 Single Source of Truth.
CODEX_REASONING_EFFORT_MAP = {
    "codex-high": "high",
    "codex-medium": "medium",
    "codex-low": "low",
}


@dataclass
class ProviderResult:
    api_base: str
    model: str
    name: str
    device_group: str
    reasoning_effort: Optional[str] = None


async def select_provider(
    task_type: str = "chat",
    tier: Optional[str] = None,
) -> ProviderResult:
    """Serverless(RunPod) + Codex 라우팅."""
    if task_type == "audio":
        return ProviderResult(RUNPOD_ASR_BASE_URL, "whisper-large-v3-turbo", "runpod-asr", "serverless")
    if task_type == "ocr":
        return ProviderResult(RUNPOD_VISION_BASE_URL, "Qwen2-VL-7B-Instruct", "runpod-vision", "serverless")
    if task_type == "embedding":
        return ProviderResult(RUNPOD_EMBED_BASE_URL, "bge-small-en-v1.5", "runpod-embed", "serverless")

    if tier == "tier-thinking":
        return ProviderResult(CODEX_API_BASE, CODEX_MODEL, "codex", "serverless", "medium")

    model = resolve_tier_to_model(tier or "tier-simple")
    return ProviderResult(RUNPOD_LLM_BASE_URL, model, "runpod-llm", "serverless")


def get_codex_provider(model_name: str = "codex-medium") -> ProviderResult:
    """Codex (CLIProxyAPI) Provider 반환.

    tier-thinking primary, codex-high/medium/low 직접 요청 시 사용.
    모델 자체는 CODEX_MODEL(env) 고정값이고, high/medium/low는 reasoning effort로 매핑된다.
    """
    effort = CODEX_REASONING_EFFORT_MAP.get(model_name, "medium")
    return ProviderResult(CODEX_API_BASE, CODEX_MODEL, "codex", "codex", effort)
