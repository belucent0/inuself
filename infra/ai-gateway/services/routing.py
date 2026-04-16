"""Tier 기반 Provider 라우팅 서비스.

Redis 세마포어 + Health Check로 Provider를 선택합니다.
Prometheus 메트릭은 사용하지 않습니다 (로깅 전용이었으므로 제거).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from config import (
    DEPLOY_MODE,
    GPU_API_BASE,
    NPU_API_BASE,
    GPU_WHISPER_CPP_API_BASE,
    GPU_INSANELY_FAST_API_BASE,
    GPU_MODEL,
    NPU_MODEL,
    GPU_AUDIO_MODEL,
    NPU_AUDIO_MODEL,
    NPU_OCR_MODEL,
    GPU_OCR_MODEL,
    DEVICE_GROUP_MAP,
    CODEX_API_BASE,
    RUNPOD_LLM_BASE_URL,
    RUNPOD_ASR_BASE_URL,
    RUNPOD_VISION_BASE_URL,
    RUNPOD_EMBED_BASE_URL,
    get_routing_policy,
    resolve_tier_to_model,
)
from services.device_lock import is_device_busy, acquire_device_lock
from services.health import check_provider_health

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    """Provider 선택 결과."""

    api_base: str
    model: str
    name: str          # provider 이름 (llama, flm, whisper-cpp 등)
    device_group: str   # "gpu" 또는 "npu"
    lock_id: Optional[str] = None  # 대기 중 획득된 lock (있으면 호출자가 해제)


def _get_provider_config(provider: str, task_type: str) -> tuple[str, str, str]:
    """(api_base, model, provider_name) 반환."""
    if provider == "npu":
        if task_type == "audio":
            return NPU_API_BASE, NPU_AUDIO_MODEL, "flm"
        if task_type == "ocr":
            return NPU_API_BASE, NPU_OCR_MODEL, "flm"
        return NPU_API_BASE, NPU_MODEL, "flm"
    else:  # gpu
        if task_type == "audio":
            return GPU_WHISPER_CPP_API_BASE, GPU_AUDIO_MODEL, "whisper-cpp"
        if task_type == "ocr":
            return GPU_API_BASE, GPU_OCR_MODEL, "llama"
        return GPU_API_BASE, GPU_MODEL, "llama"


async def _wait_for_available(
    primary: str,
    fallback: str,
    task_type: str,
    timeout: float = 30.0,
) -> ProviderResult:
    """둘 다 busy일 때 사용 가능한 Provider를 대기."""
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        if not await is_device_busy(primary):
            api_base, model, name = _get_provider_config(primary, task_type)
            return ProviderResult(api_base, model, name, primary)

        if not await is_device_busy(fallback):
            api_base, model, name = _get_provider_config(fallback, task_type)
            return ProviderResult(api_base, model, name, fallback)

        await asyncio.sleep(0.5)

    # 타임아웃 → primary 강제
    logger.warning(f"[Routing] Wait timeout, forcing {primary}")
    api_base, model, name = _get_provider_config(primary, task_type)
    return ProviderResult(api_base, model, name, primary)


async def select_provider(
    task_type: str = "chat",
    tier: Optional[str] = None,
    force_provider: Optional[str] = None,
) -> ProviderResult:
    """사용 가능한 Provider를 선택합니다.

    Args:
        task_type: "chat", "audio", "ocr"
        tier: 티어명 (예: "tier-simple", "tier-thinking")
        force_provider: 강제 선택 ("gpu" 또는 "npu")

    Returns:
        ProviderResult (api_base, model, name, device_group)
    """
    # 서버리스 모드
    if DEPLOY_MODE == "serverless":
        return _select_serverless_provider(task_type, tier)

    # 강제 선택
    if force_provider:
        api_base, model, name = _get_provider_config(force_provider, task_type)
        device_group = DEVICE_GROUP_MAP.get(name, force_provider)
        logger.info(f"[Routing] Forced: {force_provider.upper()} (task={task_type})")
        return ProviderResult(api_base, model, name, device_group)

    # Tier 정책 기반 라우팅
    policy = get_routing_policy(tier)
    primary = policy["primary"]
    fallback = policy["fallback"]
    queue_on_busy = policy.get("queue_on_busy", True)

    logger.info(
        f"[Routing] Tier: {tier or 'default'} → "
        f"{primary.upper()} primary, {fallback.upper()} fallback"
    )

    # Busy 상태 확인 (Redis 세마포어)
    primary_busy = await is_device_busy(primary)
    fallback_busy = await is_device_busy(fallback)

    if not primary_busy:
        selected = primary
        logger.info(f"[Routing] {primary.upper()} available → Selected")
    elif not fallback_busy:
        selected = fallback
        logger.info(f"[Routing] {primary.upper()} busy, {fallback.upper()} available → Fallback")
    elif queue_on_busy:
        logger.info("[Routing] Both busy, waiting...")
        return await _wait_for_available(primary, fallback, task_type)
    else:
        selected = primary
        logger.warning(f"[Routing] Both busy, no queue → forcing {primary.upper()}")

    # Health Check
    api_base, model, name = _get_provider_config(selected, task_type)
    device_group = DEVICE_GROUP_MAP.get(name, selected)

    if not await check_provider_health(name):
        # Primary unhealthy → Fallback 시도
        other = fallback if selected == primary else primary
        other_api, other_model, other_name = _get_provider_config(other, task_type)

        if await check_provider_health(other_name):
            logger.warning(
                f"[Routing] {selected.upper()} unhealthy, "
                f"{other.upper()} healthy → Fast Failover"
            )
            return ProviderResult(
                other_api, other_model, other_name,
                DEVICE_GROUP_MAP.get(other_name, other),
            )
        else:
            logger.warning(f"[Routing] Both unhealthy, using {selected.upper()} anyway")

    return ProviderResult(api_base, model, name, device_group)


def _select_serverless_provider(task_type: str, tier: Optional[str]) -> ProviderResult:
    """서버리스 모드 Provider 선택."""
    if task_type == "audio":
        return ProviderResult(
            api_base=RUNPOD_ASR_BASE_URL,
            model="whisper-large-v3-turbo",
            name="runpod-asr",
            device_group="serverless",
        )
    if task_type == "ocr":
        return ProviderResult(
            api_base=RUNPOD_VISION_BASE_URL,
            model="Qwen2-VL-7B-Instruct",
            name="runpod-vision",
            device_group="serverless",
        )
    if task_type == "embedding":
        return ProviderResult(
            api_base=RUNPOD_EMBED_BASE_URL,
            model="bge-small-en-v1.5",
            name="runpod-embed",
            device_group="serverless",
        )

    # LLM — Codex 모델은 별도 처리
    if tier == "tier-thinking":
        return ProviderResult(
            api_base=CODEX_API_BASE,
            model="gpt-5-codex(medium)",
            name="codex",
            device_group="serverless",
        )

    model = resolve_tier_to_model(tier or "tier-simple")
    return ProviderResult(
        api_base=RUNPOD_LLM_BASE_URL,
        model=model,
        name="runpod-llm",
        device_group="serverless",
    )


def get_codex_provider(model_name: str = "codex-medium") -> ProviderResult:
    """Codex (CLIProxyAPI) Provider 반환.

    tier-thinking의 primary, codex-high/medium/low 직접 요청 시 사용.
    """
    # codex-high → gpt-5-codex(high) 등 매핑
    model_map = {
        "codex-high": "gpt-5-codex(high)",
        "codex-medium": "gpt-5-codex(medium)",
        "codex-low": "gpt-5-codex(low)",
    }
    model = model_map.get(model_name, "gpt-5-codex(medium)")

    return ProviderResult(
        api_base=CODEX_API_BASE,
        model=model,
        name="codex",
        device_group="codex",
    )
