"""LiteLLM Custom Handler - Prometheus 기반 GPU/NPU 라우팅.

사용량이 낮은 Provider를 선택하여 요청을 전달합니다. (진짜 스트리밍 지원)
"""
import os
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Iterator, AsyncIterator, Optional, Any

import httpx
import redis
import redis.asyncio as redis_async

import litellm
from litellm import CustomLLM
from litellm.types.utils import GenericStreamingChunk, ModelResponse

# Monkeypatch removed to avoid Enum validation error
# if not hasattr(litellm, "provider_list"):
#     litellm.provider_list = []
# if "prometheus-router" not in litellm.provider_list:
#     litellm.provider_list.append("prometheus-router")

logger = logging.getLogger(__name__)

# 환경변수
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://asr-prometheus:9090")
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-redis:6379/0")


# 기본값은 호스트 Docker 내부 주소 (LiteLLM 컨테이너 -> 호스트)
GPU_API_BASE = os.getenv("GPU_API_BASE", "http://host.docker.internal:8080")
NPU_API_BASE = os.getenv("NPU_API_BASE", "http://host.docker.internal:11434")  # ASR 기본
NPU_LLM_API_BASE = os.getenv("NPU_LLM_API_BASE", "http://host.docker.internal:11435")  # LLM (flm-llm-server)

# 장치 ID
GPU_DEVICE_ID = os.getenv("GPU_DEVICE_ID", "0x000142B6")
NPU_DEVICE_ID = os.getenv("NPU_DEVICE_ID", "0x000160E6")

# Chat 모델명 (llama-server 멀티모델: 파일명 사용)
GPU_MODEL = os.getenv("GPU_MODEL", "Qwen3-4B-Instruct-2507-Q4_K_S.gguf")
NPU_MODEL = os.getenv("NPU_MODEL", "qwen3-it:4b")

# Audio 설정
GPU_AUDIO_API_BASE = os.getenv("GPU_AUDIO_API_BASE", "http://host.docker.internal:8001")
NPU_AUDIO_API_BASE = os.getenv("NPU_AUDIO_API_BASE", "http://host.docker.internal:11434")
GPU_AUDIO_MODEL = os.getenv("GPU_AUDIO_MODEL", "whisper-turbo")
NPU_AUDIO_MODEL = os.getenv("NPU_AUDIO_MODEL", "flm-audio")
GPU_WHISPER_CPP_API_BASE = os.getenv("GPU_WHISPER_CPP_API_BASE", "http://host.docker.internal:8001")
GPU_INSANELY_FAST_API_BASE = os.getenv("GPU_INSANELY_FAST_API_BASE", "http://host.docker.internal:8002")

# 임계값
BUSY_THRESHOLD = 70  # 70% 이상이면 "바쁨"

# Health check 설정
HEALTH_CHECK_TIMEOUT = float(os.getenv("HEALTH_CHECK_TIMEOUT", "3.0"))  # 서버 응답 대기 시간
HEALTH_CHECK_ENABLED = os.getenv("HEALTH_CHECK_ENABLED", "true").lower() == "true"

# GPU OCR Vision 설정
GPU_OCR_API_BASE = os.getenv("GPU_OCR_API_BASE", "http://host.docker.internal:8081")

# Provider별 Health Check URL 매핑
PROVIDER_HEALTH_URLS = {
    "llama": f"{GPU_API_BASE}/health",
    "llama-ocr": f"{GPU_OCR_API_BASE}/health",  # GPU Vision OCR (Qwen3-VL-8B)
    "flm": f"{NPU_API_BASE}/v1/models",  # FLM ASR (11434)
    "flm-llm": f"{NPU_LLM_API_BASE}/v1/models",  # FLM LLM (11435)
    "whisper-cpp": f"{GPU_WHISPER_CPP_API_BASE}",  # whisper.cpp 루트는 health 역할
    "insanely-fast": f"{GPU_INSANELY_FAST_API_BASE}/health",
    "diarization-server": "http://host.docker.internal:8003/health",
}

# GPU 세마포어 키 (Worker와 동일)
GPU_SEMAPHORE_KEY = "worker:gpu:active"

# Redis 클라이언트 (Connection Pool 사용)
try:
    redis_client_sync = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client_async = redis_async.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logger.error(f"Failed to initialize Redis clients: {e}")
    redis_client_sync = None
    redis_client_async = None


def query_prometheus_sync(device_id: str) -> float:
    """Prometheus에서 5초 평균 사용량 조회 (동기)."""
    # 쿼리를 한 줄로 작성. sum(avg_over_time(...[5s])) 형태로 수정
    query = f'sum(avg_over_time(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{device_id}.*engtype_Compute.*"}}[5s]))'
    
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "success" and data["data"]["result"]:
                # result는 벡터일 수 있음. 값이 없으면 0
                if not data["data"]["result"]:
                    return 0.0
                value = float(data["data"]["result"][0]["value"][1])
                logger.debug(f"[Prometheus] {device_id}: {value:.1f}%")
                return value
            logger.debug(f"[Prometheus] {device_id}: no data, returning 0")
            return 0.0
    except Exception as e:
        logger.warning(f"[Prometheus] Query failed for {device_id}: {e}")
        return 0.0


async def query_prometheus_async(device_id: str) -> float:
    """Prometheus에서 5초 평균 사용량 조회 (비동기)."""
    query = f'sum(avg_over_time(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{device_id}.*engtype_Compute.*"}}[5s]))'

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                if not data["data"]["result"]:
                    return 0.0
                value = float(data["data"]["result"][0]["value"][1])
                logger.debug(f"[Prometheus] {device_id}: {value:.1f}%")
                return value
            return 0.0
    except Exception as e:
        logger.warning(f"[Prometheus] Query failed for {device_id}: {e}")
        return 0.0


def check_provider_health_sync(provider: str) -> bool:
    """Provider 서버가 현재 실행 중인지 확인 (동기).

    Args:
        provider: 'llama', 'flm', 'whisper-cpp', 'insanely-fast', 'diarization-server'

    Returns:
        서버가 응답하면 True, 그렇지 않으면 False
    """
    if not HEALTH_CHECK_ENABLED:
        return True  # Health check 비활성화 시 항상 True

    health_url = PROVIDER_HEALTH_URLS.get(provider)
    if not health_url:
        logger.warning(f"[HealthCheck] No health URL for provider: {provider}")
        return True  # URL 없으면 체크 건너뜀

    try:
        with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
            response = client.get(health_url)
            is_healthy = response.status_code == 200
            logger.debug(f"[HealthCheck] {provider}: {'healthy' if is_healthy else 'unhealthy'}")
            return is_healthy
    except Exception as e:
        logger.debug(f"[HealthCheck] {provider}: unreachable ({e})")
        return False


async def check_provider_health_async(provider: str) -> bool:
    """Provider 서버가 현재 실행 중인지 확인 (비동기).

    Args:
        provider: 'llama', 'flm', 'whisper-cpp', 'insanely-fast', 'diarization-server'

    Returns:
        서버가 응답하면 True, 그렇지 않으면 False
    """
    if not HEALTH_CHECK_ENABLED:
        return True  # Health check 비활성화 시 항상 True

    health_url = PROVIDER_HEALTH_URLS.get(provider)
    if not health_url:
        logger.warning(f"[HealthCheck] No health URL for provider: {provider}")
        return True  # URL 없으면 체크 건너뜀

    try:
        async with httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT) as client:
            response = await client.get(health_url)
            is_healthy = response.status_code == 200
            logger.debug(f"[HealthCheck] {provider}: {'healthy' if is_healthy else 'unhealthy'}")
            return is_healthy
    except Exception as e:
        logger.debug(f"[HealthCheck] {provider}: unreachable ({e})")
        return False


def is_gpu_busy_sync() -> tuple[bool, str]:
    """GPU 세마포어 확인 (동기).

    Returns:
        (is_busy, reason) - GPU가 사용 중이면 (True, reason), 아니면 (False, "")
    """
    if not redis_client_sync:
        return False, ""
    try:
        reason = redis_client_sync.get(GPU_SEMAPHORE_KEY)
        if reason:
            logger.info(f"[GPU Semaphore] GPU is busy: {reason}")
            return True, reason
        return False, ""
    except Exception as e:
        logger.warning(f"[GPU Semaphore] Check failed: {e}")
        return False, ""


async def is_gpu_busy_async() -> tuple[bool, str]:
    """GPU 세마포어 확인 (비동기).

    Returns:
        (is_busy, reason) - GPU가 사용 중이면 (True, reason), 아니면 (False, "")
    """
    if not redis_client_async:
        return False, ""
    try:
        reason = await redis_client_async.get(GPU_SEMAPHORE_KEY)
        if reason:
            logger.info(f"[GPU Semaphore] GPU is busy: {reason}")
            return True, reason
        return False, ""
    except Exception as e:
        logger.warning(f"[GPU Semaphore] Check failed: {e}")
        return False, ""


async def send_provider_control_signal(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송 (비동기).

    Args:
        provider: 'flm', 'llama', 'whisper-cpp', 'insanely-fast', 'diarization-server'
        action: 'start' or 'touch' (touch = 활동 타임스탬프 갱신, idle timeout 리셋)
    """
    if not redis_client_async:
        logger.warning("[CustomRouter] Redis not available, skipping provider control signal")
        return

    try:
        message = {"action": action, "provider": provider}
        await redis_client_async.publish("provider.control", json.dumps(message))
        logger.debug(f"[CustomRouter] Sent provider signal: {provider} -> {action}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to send provider signal: {e}")


async def increment_active_count(provider: str):
    """Provider 활성 요청 카운트 증가 (비동기)."""
    if not redis_client_async:
        return
    try:
        key = f"provider:{provider}:active_count"
        await redis_client_async.incr(key)
        logger.debug(f"[CustomRouter] INCR {key}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to increment active count: {e}")


async def decrement_active_count(provider: str):
    """Provider 활성 요청 카운트 감소 (비동기)."""
    if not redis_client_async:
        return
    try:
        key = f"provider:{provider}:active_count"
        # DECR 후 0 미만이면 0으로 리셋
        new_val = await redis_client_async.decr(key)
        if new_val < 0:
            await redis_client_async.set(key, 0)
        logger.debug(f"[CustomRouter] DECR {key} -> {max(0, new_val)}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to decrement active count: {e}")


async def select_provider_async(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
    skip_signal: bool = False,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (비동기).

    Args:
        task_type: "chat" or "audio"
        force_provider: 강제 선택 - "gpu" or "npu" (None이면 자동 선택)
        skip_signal: Provider 시작 신호 전송 여부 (True면 호출자가 직접 전송)

    Returns:
        (api_base, model, provider_name)

    라우팅 우선순위 (하이브리드 방식):
    1. 강제 선택 (force_provider) → 해당 Provider 사용
    2. Redis 세마포어 → NPU/GPU 사용 중이면 다른 쪽 선택
    3. 사용률 기반 우선 Provider 결정 (NPU 우선)
    4. 선택된 Provider unhealthy → start 신호 + 대기 (max 15초)
    5. timeout → fallback Provider 시도 (동일 로직)
    """
    # Provider 설정 헬퍼
    def get_provider_config(provider: str, task: str) -> tuple[str, str, str, str]:
        """(api_base, model, provider_key, signal_provider)"""
        if provider == "npu":
            if task == "audio":
                return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio", "flm"
            return NPU_LLM_API_BASE, NPU_MODEL, "npu", "flm-llm"  # LLM은 11435 포트
        else:  # gpu
            if task == "audio":
                return GPU_WHISPER_CPP_API_BASE, GPU_AUDIO_MODEL, "gpu-audio", "whisper-cpp"
            return GPU_API_BASE, GPU_MODEL, "gpu", "llama"

    # 0. 강제 선택이 있으면 바로 반환
    if force_provider == "gpu":
        logger.info(f"[CustomRouter] Forced: GPU (task_type={task_type})")
        if not skip_signal:
            await send_provider_control_signal("llama", "start")
        if task_type == "audio":
            return GPU_WHISPER_CPP_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
        return GPU_API_BASE, GPU_MODEL, "gpu"
    elif force_provider == "npu":
        logger.info(f"[CustomRouter] Forced: NPU (task_type={task_type})")
        if not skip_signal:
            await send_provider_control_signal("flm-llm", "start")
        if task_type == "audio":
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_LLM_API_BASE, NPU_MODEL, "npu"  # LLM은 11435 포트

    # 1. Redis 세마포어 체크 (즉시 반응)
    # 1-1. NPU 세마포어 체크 → GPU로 강제
    if redis_client_async:
        try:
            npu_active = await redis_client_async.exists("worker:npu:active")
            if npu_active:
                logger.info(f"[CustomRouter] NPU Semaphore Active → Forced GPU")
                api_base, model, key, signal = get_provider_config("gpu", task_type)
                if not skip_signal:
                    await send_provider_control_signal(signal, "start")
                return api_base, model, key
        except Exception as e:
            logger.warning(f"Redis NPU semaphore check failed: {e}")

    # 1-2. GPU 세마포어 체크 → NPU로 강제
    gpu_busy, gpu_reason = await is_gpu_busy_async()
    if gpu_busy:
        logger.info(f"[CustomRouter] GPU Semaphore Active ({gpu_reason}) → Forced NPU")
        api_base, model, key, signal = get_provider_config("npu", task_type)
        if not skip_signal:
            await send_provider_control_signal(signal, "start")
        return api_base, model, key

    # 2. Prometheus 메트릭 + Health Check
    gpu_avg = await query_prometheus_async(GPU_DEVICE_ID)
    npu_avg = await query_prometheus_async(NPU_DEVICE_ID)
    # Chat task는 flm-llm (11435), Audio task는 flm (11434) 사용
    npu_health_provider = "flm-llm" if task_type == "chat" else "flm"
    npu_healthy = await check_provider_health_async(npu_health_provider)
    gpu_healthy = await check_provider_health_async("llama")

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}% | Health - NPU({npu_health_provider}): {npu_healthy}, GPU: {gpu_healthy}")

    # 3. 사용률 기반 우선 Provider 결정 (NPU 우선)
    if npu_avg < BUSY_THRESHOLD:
        primary, fallback = "npu", "gpu"
    elif gpu_avg < BUSY_THRESHOLD:
        primary, fallback = "gpu", "npu"
    else:
        # 둘 다 바쁘면 NPU 우선
        primary, fallback = "npu", "gpu"

    logger.info(f"[CustomRouter] Priority: {primary.upper()} (fallback: {fallback.upper()})")

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    if primary_healthy:
        # 이미 ready → 바로 사용
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (already healthy)")
        if not skip_signal:
            await send_provider_control_signal(primary_signal, "start")
        return primary_api, primary_model, primary_key

    # Primary unhealthy → start 신호 + 대기 (max 15초)
    logger.info(f"[CustomRouter] {primary.upper()} unhealthy, sending start signal...")
    await send_provider_control_signal(primary_signal, "start")

    if await wait_for_server_ready_async(primary_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (started successfully)")
        return primary_api, primary_model, primary_key

    # 5. Fallback Provider 시도
    logger.warning(f"[CustomRouter] {primary.upper()} failed to start, trying {fallback.upper()}...")
    fallback_api, fallback_model, fallback_key, fallback_signal = get_provider_config(fallback, task_type)
    fallback_healthy = gpu_healthy if fallback == "gpu" else npu_healthy

    if fallback_healthy:
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (already healthy)")
        if not skip_signal:
            await send_provider_control_signal(fallback_signal, "start")
        return fallback_api, fallback_model, fallback_key

    # Fallback도 unhealthy → start 신호 + 대기 (max 15초)
    logger.info(f"[CustomRouter] {fallback.upper()} unhealthy, sending start signal...")
    await send_provider_control_signal(fallback_signal, "start")

    if await wait_for_server_ready_async(fallback_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (started successfully)")
        return fallback_api, fallback_model, fallback_key

    # 6. 둘 다 실패 → Primary 반환 (호출자가 에러 처리)
    logger.error(f"[CustomRouter] Both providers failed to start! Returning {primary.upper()} anyway...")
    return primary_api, primary_model, primary_key


def send_provider_control_signal_sync(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송 (동기)."""
    if not redis_client_sync:
        logger.warning("[CustomRouter] Redis not available, skipping provider control signal")
        return

    try:
        message = {"action": action, "provider": provider}
        redis_client_sync.publish("provider.control", json.dumps(message))
        logger.debug(f"[CustomRouter] Sent provider signal (sync): {provider} -> {action}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to send provider signal: {e}")


def wait_for_server_ready_sync(api_base: str, max_wait: float = 60.0, interval: float = 1.0) -> bool:
    """서버가 준비될 때까지 대기 (동기).

    Args:
        api_base: 서버 기본 URL (예: http://host.docker.internal:11434)
        max_wait: 최대 대기 시간 (초)
        interval: 체크 간격 (초)

    Returns:
        서버 준비 완료 여부
    """
    # Health endpoint 결정
    if ":11434" in api_base or ":11435" in api_base or ":11436" in api_base:
        # FLM 서버 (ASR:11434, LLM:11435, OCR:11436): /v1/models 엔드포인트로 체크
        health_url = f"{api_base}/v1/models"
    elif ":8080" in api_base:
        # llama-server: /health 엔드포인트
        health_url = f"{api_base}/health"
    else:
        health_url = f"{api_base}/health"

    start_time = time.time()
    attempt = 0

    while (time.time() - start_time) < max_wait:
        attempt += 1
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(health_url)
                if response.status_code == 200:
                    elapsed = time.time() - start_time
                    logger.info(f"[CustomRouter] Server ready at {api_base} after {elapsed:.1f}s (attempt {attempt})")
                    return True
        except Exception as e:
            if attempt == 1:
                logger.info(f"[CustomRouter] Waiting for server at {api_base}...")
            logger.debug(f"[CustomRouter] Server not ready (attempt {attempt}): {e}")

        time.sleep(interval)

    logger.warning(f"[CustomRouter] Server at {api_base} not ready after {max_wait}s")
    return False


async def wait_for_server_ready_async(api_base: str, max_wait: float = 60.0, interval: float = 1.0) -> bool:
    """서버가 준비될 때까지 대기 (비동기).

    Args:
        api_base: 서버 기본 URL (예: http://host.docker.internal:11434)
        max_wait: 최대 대기 시간 (초)
        interval: 체크 간격 (초)

    Returns:
        서버 준비 완료 여부
    """
    import asyncio

    # Health endpoint 결정
    if ":11434" in api_base or ":11435" in api_base or ":11436" in api_base:
        # FLM 서버 (ASR:11434, LLM:11435, OCR:11436): /v1/models 엔드포인트로 체크
        health_url = f"{api_base}/v1/models"
    elif ":8080" in api_base:
        # llama-server: /health 엔드포인트
        health_url = f"{api_base}/health"
    else:
        health_url = f"{api_base}/health"

    start_time = time.time()
    attempt = 0

    while (time.time() - start_time) < max_wait:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(health_url)
                if response.status_code == 200:
                    elapsed = time.time() - start_time
                    logger.info(f"[CustomRouter] Server ready at {api_base} after {elapsed:.1f}s (attempt {attempt})")
                    return True
        except Exception as e:
            if attempt == 1:
                logger.info(f"[CustomRouter] Waiting for server at {api_base}...")
            logger.debug(f"[CustomRouter] Server not ready (attempt {attempt}): {e}")

        await asyncio.sleep(interval)

    logger.warning(f"[CustomRouter] Server at {api_base} not ready after {max_wait}s")
    return False


def increment_active_count_sync(provider: str):
    """Provider 활성 요청 카운트 증가 (동기)."""
    if not redis_client_sync:
        return
    try:
        key = f"provider:{provider}:active_count"
        redis_client_sync.incr(key)
        logger.debug(f"[CustomRouter] INCR {key}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to increment active count: {e}")


def decrement_active_count_sync(provider: str):
    """Provider 활성 요청 카운트 감소 (동기)."""
    if not redis_client_sync:
        return
    try:
        key = f"provider:{provider}:active_count"
        new_val = redis_client_sync.decr(key)
        if new_val < 0:
            redis_client_sync.set(key, 0)
        logger.debug(f"[CustomRouter] DECR {key} -> {max(0, new_val)}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to decrement active count: {e}")


def select_provider_sync(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
    skip_signal: bool = False,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (동기).

    라우팅 우선순위 (하이브리드 방식):
    1. 강제 선택 (force_provider) → 해당 Provider 사용
    2. Redis 세마포어 → NPU/GPU 사용 중이면 다른 쪽 선택
    3. 사용률 기반 우선 Provider 결정 (NPU 우선)
    4. 선택된 Provider unhealthy → start 신호 + 대기 (max 15초)
    5. timeout → fallback Provider 시도 (동일 로직)
    """
    # Provider 설정 헬퍼
    def get_provider_config(provider: str, task: str) -> tuple[str, str, str, str]:
        """(api_base, model, provider_key, signal_provider)"""
        if provider == "npu":
            if task == "audio":
                return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio", "flm"
            return NPU_LLM_API_BASE, NPU_MODEL, "npu", "flm-llm"  # LLM은 11435 포트
        else:  # gpu
            if task == "audio":
                return GPU_WHISPER_CPP_API_BASE, GPU_AUDIO_MODEL, "gpu-audio", "whisper-cpp"
            return GPU_API_BASE, GPU_MODEL, "gpu", "llama"

    # 0. 강제 선택이 있으면 바로 반환
    if force_provider == "gpu":
        logger.info(f"[CustomRouter] Forced: GPU (task_type={task_type})")
        if not skip_signal:
            send_provider_control_signal_sync("llama", "start")
        if task_type == "audio":
            return GPU_WHISPER_CPP_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
        return GPU_API_BASE, GPU_MODEL, "gpu"
    elif force_provider == "npu":
        logger.info(f"[CustomRouter] Forced: NPU (task_type={task_type})")
        if not skip_signal:
            send_provider_control_signal_sync("flm-llm", "start")
        if task_type == "audio":
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_LLM_API_BASE, NPU_MODEL, "npu"  # LLM은 11435 포트

    # 1. Redis 세마포어 체크 (즉시 반응)
    # 1-1. NPU 세마포어 체크 → GPU로 강제
    if redis_client_sync:
        try:
            npu_active = redis_client_sync.exists("worker:npu:active")
            if npu_active:
                logger.info(f"[CustomRouter] NPU Semaphore Active → Forced GPU")
                api_base, model, key, signal = get_provider_config("gpu", task_type)
                if not skip_signal:
                    send_provider_control_signal_sync(signal, "start")
                return api_base, model, key
        except Exception as e:
            logger.warning(f"Redis NPU semaphore check failed: {e}")

    # 1-2. GPU 세마포어 체크 → NPU로 강제
    gpu_busy, gpu_reason = is_gpu_busy_sync()
    if gpu_busy:
        logger.info(f"[CustomRouter] GPU Semaphore Active ({gpu_reason}) → Forced NPU")
        api_base, model, key, signal = get_provider_config("npu", task_type)
        if not skip_signal:
            send_provider_control_signal_sync(signal, "start")
        return api_base, model, key

    # 2. Prometheus 메트릭 + Health Check
    gpu_avg = query_prometheus_sync(GPU_DEVICE_ID)
    npu_avg = query_prometheus_sync(NPU_DEVICE_ID)
    # Chat task는 flm-llm (11435), Audio task는 flm (11434) 사용
    npu_health_provider = "flm-llm" if task_type == "chat" else "flm"
    npu_healthy = check_provider_health_sync(npu_health_provider)
    gpu_healthy = check_provider_health_sync("llama")

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}% | Health - NPU({npu_health_provider}): {npu_healthy}, GPU: {gpu_healthy}")

    # 3. 사용률 기반 우선 Provider 결정 (NPU 우선)
    if npu_avg < BUSY_THRESHOLD:
        primary, fallback = "npu", "gpu"
    elif gpu_avg < BUSY_THRESHOLD:
        primary, fallback = "gpu", "npu"
    else:
        # 둘 다 바쁘면 NPU 우선
        primary, fallback = "npu", "gpu"

    logger.info(f"[CustomRouter] Priority: {primary.upper()} (fallback: {fallback.upper()})")

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    if primary_healthy:
        # 이미 ready → 바로 사용
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (already healthy)")
        if not skip_signal:
            send_provider_control_signal_sync(primary_signal, "start")
        return primary_api, primary_model, primary_key

    # Primary unhealthy → start 신호 + 대기 (max 15초)
    logger.info(f"[CustomRouter] {primary.upper()} unhealthy, sending start signal...")
    send_provider_control_signal_sync(primary_signal, "start")

    if wait_for_server_ready_sync(primary_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (started successfully)")
        return primary_api, primary_model, primary_key

    # 5. Fallback Provider 시도
    logger.warning(f"[CustomRouter] {primary.upper()} failed to start, trying {fallback.upper()}...")
    fallback_api, fallback_model, fallback_key, fallback_signal = get_provider_config(fallback, task_type)
    fallback_healthy = gpu_healthy if fallback == "gpu" else npu_healthy

    if fallback_healthy:
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (already healthy)")
        if not skip_signal:
            send_provider_control_signal_sync(fallback_signal, "start")
        return fallback_api, fallback_model, fallback_key

    # Fallback도 unhealthy → start 신호 + 대기 (max 15초)
    logger.info(f"[CustomRouter] {fallback.upper()} unhealthy, sending start signal...")
    send_provider_control_signal_sync(fallback_signal, "start")

    if wait_for_server_ready_sync(fallback_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (started successfully)")
        return fallback_api, fallback_model, fallback_key

    # 6. 둘 다 실패 → Primary 반환 (호출자가 에러 처리)
    logger.error(f"[CustomRouter] Both providers failed to start! Returning {primary.upper()} anyway...")
    return primary_api, primary_model, primary_key



class PrometheusRouter(CustomLLM):
    """Prometheus 메트릭 기반 GPU/NPU 라우터."""
    streaming = True
    
    def completion(self, *args, **kwargs) -> ModelResponse:
        """동기 completion (Non-streaming)."""
        messages = kwargs.get("messages", [])
        
        litellm_params = kwargs.get("litellm_params", {})
        optional_params = kwargs.get("optional_params", {})
        stream = kwargs.get("stream", False) or litellm_params.get("stream") or optional_params.get("stream")
        
        logger.info(f"[PrometheusRouter] completion called. stream={stream}")
        if stream:
             logger.warning("[PrometheusRouter] stream=True detected in completion. LiteLLM should have called astreaming.")

        # On-Demand: Provider 선택 및 시작 (Signal은 내부에서 skip하고 직접 제어할 수도 있지만, sync는 select 내부에서 처리)
        # 하지만 종료를 위해 provider 이름을 알아야 하므로 반환값 사용
        api_base, model, provider_key = select_provider_sync(task_type="chat")

        # NOTE: select_provider_sync calls start signal. We need to map provider_key to signal name if different.
        # provider_key: 'npu', 'gpu', 'npu-audio', 'gpu-audio'
        target_provider = "flm" if "npu" in provider_key else "llama"

        logger.info(f"[PrometheusRouter] Routing to {target_provider} ({api_base})")

        # 서버 준비 대기 (최대 60초)
        if not wait_for_server_ready_sync(api_base, max_wait=60.0):
            logger.warning(f"[PrometheusRouter] Server not ready, proceeding anyway...")

        # On-Demand: 활성 요청 카운트 증가
        increment_active_count_sync(target_provider)

        url = f"{api_base}/v1/chat/completions"
        payload = {"model": model, "messages": messages, "stream": False}

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()

                return ModelResponse(
                    id=result.get("id", f"chatcmpl-{uuid.uuid4()}"),
                    created=result.get("created", int(time.time())),
                    model=result.get("model", model),
                    object="chat.completion",
                    choices=[
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": result["choices"][0]["message"]["content"],
                            },
                            "finish_reason": result["choices"][0].get("finish_reason", "stop"),
                        }
                    ],
                    usage=result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
                )
        finally:
            # On-Demand: 활성 요청 카운트 감소 + touch (idle timeout 리셋)
            decrement_active_count_sync(target_provider)
            send_provider_control_signal_sync(target_provider, "touch")

    async def acompletion(self, *args, **kwargs) -> ModelResponse:
        """비동기 completion (Non-streaming)."""
        # ... logic mainly same as completion but async ...
        # For simplicity calling self.completion (sync) but this breaks async pattern for On-Demand.
        # Should implement true async On-Demand here if possible, but fallback to completion is safe for now.
        return self.completion(*args, **kwargs)
    
    async def astreaming(self, *args, **kwargs) -> AsyncIterator[GenericStreamingChunk]:
        """비동기 스트리밍 (Real Streaming)."""
        start_ts = time.time()
        
        # model parameter extraction for logging
        model = kwargs.get("model", "")
        if not model:
            litellm_params = kwargs.get("litellm_params", {})
            model = litellm_params.get("model", "unknown")
            
        litellm_params = kwargs.get("litellm_params", {})
        metadata = litellm_params.get("metadata", {}) or {}
        trace_id = metadata.get("trace_id", "unknown")
            
        def get_log_prefix():
            return f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}][{trace_id}]"

        logger.info(f"{get_log_prefix()} [PrometheusRouter] Request START: {model}")
        
        messages = kwargs.get("messages", [])
        
        # On-Demand Start
        api_base, selected_model, provider_key = await select_provider_async(task_type="chat")
        target_provider = "flm" if "npu" in provider_key else "llama"

        # Provider selection time
        selection_latency = time.time() - start_ts
        logger.debug(f"{get_log_prefix()} [PrometheusRouter] Provider selected: {target_provider} (Latency: {selection_latency:.3f}s)")

        # 서버 준비 대기 (최대 60초)
        if not await wait_for_server_ready_async(api_base, max_wait=60.0):
            logger.warning(f"{get_log_prefix()} [PrometheusRouter] Server not ready, proceeding anyway...")

        # On-Demand: 활성 요청 카운트 증가
        await increment_active_count(target_provider)

        url = f"{api_base}/v1/chat/completions"
        payload = {"model": selected_model, "messages": messages, "stream": True}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                try:
                    async with client.stream("POST", url, json=payload) as response:
                        response.raise_for_status()

                        ttfb_latency = time.time() - start_ts
                        logger.info(f"{get_log_prefix()} [PrometheusRouter] Response START (TTFB): {ttfb_latency:.3f}s")

                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                data_str = line[6:]
                                if data_str.strip() == "[DONE]":
                                    break
                                try:
                                    data = json.loads(data_str)
                                    choice = data["choices"][0]
                                    delta = choice.get("delta", {})
                                    content = delta.get("content", "")

                                    yield GenericStreamingChunk(
                                        text=content,
                                        is_finished=choice.get("finish_reason") is not None,
                                        finish_reason=choice.get("finish_reason"),
                                        usage=None,
                                    )
                                except Exception as e:
                                    logger.error(f"{get_log_prefix()} Streaming parse error: {e}")

                    total_duration = time.time() - start_ts
                    logger.info(f"{get_log_prefix()} [PrometheusRouter] Request END: {model} (Total: {total_duration:.3f}s)")

                except Exception as e:
                    total_duration = time.time() - start_ts
                    logger.error(f"{get_log_prefix()} [PrometheusRouter] Request FAILED: {model} (Total: {total_duration:.3f}s, Error: {e})")
                    raise e
        finally:
            # On-Demand: 활성 요청 카운트 감소 + touch (idle timeout 리셋)
            await decrement_active_count(target_provider)
            await send_provider_control_signal(target_provider, "touch")

    async def transcription(self, *args, **kwargs) -> ModelResponse:
        """Audio Transcription."""
        # Prepare multipart/form-data
        files = kwargs.get("files", {})
        data = kwargs.get("data", {})
        requested_model = data.get("model", "")

        # 0. Diarization 라우팅 (model="pyannote")
        if requested_model.endswith("pyannote"):
            import httpx
            logger.info(f"[PrometheusRouter] Routing to Diarization Server: model={requested_model}")

            # Diarization Server 시작 신호 전송 + 활성 카운트 증가
            await send_provider_control_signal("diarization-server", "start")
            await increment_active_count("diarization-server")

            diarization_url = f"http://host.docker.internal:8003/v1/audio/diarization"

            try:
                # 파일 포인터 되감기
                file.seek(0)

                # 타임아웃 넉넉하게
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    # 프로세스 시작 대기 (간단히 sleep)
                    await asyncio.sleep(2.0)

                    logger.info(f"[PrometheusRouter] Routing to diarization server ({diarization_url})")
                    try:
                        response = await client.post(
                            diarization_url,
                            files={"file": (file.name, file, "audio/wav")},
                            data={"model": requested_model, **kwargs}
                        )
                        response.raise_for_status()
                        return response.json()
                    except httpx.HTTPStatusError as e:
                        logger.error(f"[PrometheusRouter] HTTP Error from diarization server: {e.response.status_code} - {e.response.text}")
                        raise
                    except Exception as e:
                        logger.error(f"[PrometheusRouter] Diarization Server Connection/Request Error: {str(e)}")
                        raise
            except Exception as e:
                logger.error(f"[PrometheusRouter] Diarization Server Error: {e}")
                raise e
            finally:
                # On-Demand: 활성 카운트 감소 + touch (idle timeout 리셋)
                await decrement_active_count("diarization-server")
                await send_provider_control_signal("diarization-server", "touch")

        # 모드 판별
        is_accuracy_mode = requested_model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3")
        is_turbo_mode = requested_model in ("whisper-turbo", "whisper-large-v3-turbo", "turbo", "openai/whisper-turbo")
        is_speed_mode = requested_model in ("flm-audio", "flm", "openai/flm-audio")

        # 1. 라우팅 대상 및 Provider 결정
        target_api_base = ""
        target_model = ""
        target_signal_provider = ""
        force_provider = None

        if is_accuracy_mode:
            force_provider = "gpu"
            target_api_base = GPU_INSANELY_FAST_API_BASE
            target_model = "whisper-large-v3"
            target_signal_provider = "insanely-fast"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> GPU Accuracy (Insanely-Fast:8002)")
        
        elif is_turbo_mode:
            force_provider = "gpu"
            target_api_base = GPU_WHISPER_CPP_API_BASE
            target_model = "whisper-turbo"
            target_signal_provider = "whisper-cpp"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> GPU Speed (Whisper.cpp:8001)")
            
        elif is_speed_mode:
            force_provider = "npu"
            target_api_base = NPU_AUDIO_API_BASE
            target_model = "flm-audio"
            target_signal_provider = "flm"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> NPU Speed (FLM:11434)")
            
        else:
            force_provider = "gpu"
            target_api_base = GPU_INSANELY_FAST_API_BASE
            target_model = "whisper-large-v3"
            target_signal_provider = "insanely-fast"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> Default: GPU Accuracy (Insanely-Fast:8002)")

        # 2. Provider 선택 (신호 생략)
        await select_provider_async(
            task_type="audio",
            force_provider=force_provider,
            skip_signal=True 
        )

        # 3. 명시적 시작 신호 전송 + 활성 카운트 증가
        await send_provider_control_signal(target_signal_provider, "start")
        await increment_active_count(target_signal_provider)
        logger.info(f"[PrometheusRouter] Transcription Target: {target_signal_provider} | URL: {target_api_base}")

        # API 호출
        try:
            url = f"{target_api_base}/v1/audio/transcriptions"
            data["model"] = target_model # 실제 백엔드가 기대하는 모델명으로 변경

            if "file" in data: del data["file"]

            try:
                # 대기 시간 증가 (모델 로딩 시간 고려)
                async with httpx.AsyncClient(timeout=1800.0) as client:
                     # 1차 시도 전 잠시 대기 (프로세스 런치 시간) - 하지만 start 신호 후 즉시라 위험할 수 있음. 
                     # Provider Manager가 프로세스가 뜰 때까지(포트 리슨) 기다려주진 않으므로, 여기서 sleep이나 retry가 필요할 수 있음.
                     # 일단은 httpx의 ConnectError 시 재시도 로직이 필요할 수 있음.
                     # 여기서는 간단히 2초 대기
                    await asyncio.sleep(2.0)
                    
                    logger.info(f"[PrometheusRouter] Posting to {url}")
                    try:
                        response = await client.post(url, data=data, files=files)
                        response.raise_for_status()
                        result = response.json()
                    except httpx.HTTPStatusError as e:
                         logger.error(f"[PrometheusRouter] HTTP Error from {target_signal_provider}: {e.response.status_code} - {e.response.text}")
                         raise e
                    except Exception as e:
                         logger.error(f"[PrometheusRouter] Connection Error to {target_signal_provider}: {e}")
                         raise e
            except Exception as e:
                # 4. Fallback 로직 (Speed 모드에서 NPU 실패 시 -> GPU Whisper.cpp)
                if is_speed_mode:
                    logger.warning(f"[PrometheusRouter] NPU failed: {e}. Trying Fallback...")
                    # FLM 활성 카운트 감소 + touch (30초 후 자동 종료)
                    await decrement_active_count("flm")
                    await send_provider_control_signal("flm", "touch")

                    # Fallback Target
                    fallback_provider = "whisper-cpp"
                    fallback_api_base = GPU_WHISPER_CPP_API_BASE
                    data["model"] = "whisper-turbo"

                    logger.info(f"[PrometheusRouter] Fallback to {fallback_provider}")
                    await send_provider_control_signal(fallback_provider, "start")
                    await increment_active_count(fallback_provider)
                    # Fallback provider로 교체
                    target_signal_provider = fallback_provider

                    await asyncio.sleep(2.0) # Wait for startup

                    try:
                        async with httpx.AsyncClient(timeout=1800.0) as client:
                            for f_key, f_val in files.items():
                                 if hasattr(f_val, 'seek'): f_val.seek(0)

                            response = await client.post(f"{fallback_api_base}/v1/audio/transcriptions", data=data, files=files)
                            response.raise_for_status()
                            result = response.json()
                    except Exception as fallback_error:
                         logger.error(f"[PrometheusRouter] Fallback failed: {fallback_error}")
                         raise fallback_error
                else:
                    logger.error(f"[PrometheusRouter] Transcription failed: {e}")
                    raise e

            return ModelResponse(
                id=result.get("id", f"transcribe-{uuid.uuid4()}"),
                created=int(time.time()),
                model=target_model,
                object="text",
                choices=[
                    {
                        "text": result.get("text", ""),
                        "segments": result.get("segments", []),
                        "language": result.get("language", ""),
                    }
                ],
            )
        finally:
            # On-Demand: 활성 카운트 감소 + touch (idle timeout 리셋)
            await decrement_active_count(target_signal_provider)
            await send_provider_control_signal(target_signal_provider, "touch")

# LiteLLM에 등록할 핸들러 인스턴스
prometheus_router = PrometheusRouter()

