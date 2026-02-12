"""LiteLLM Custom Handler - Prometheus 기반 GPU/NPU 라우팅.

Architecture V6.6: Redis Stream 기반 메시징 아키텍처

주요 변경 (V6.6):
- Docker → Host HTTP 통신 제거 (Docker Desktop 크래시 방지)
- Redis Stream을 통한 GPU 작업 요청/응답
- Provider Manager가 Host에서 실행되어 localhost로 GPU 서버 접근

ASR 라우팅:
- 신속모드: whisper-cpp (GPU, 8001) - whisper v3 turbo
- 정확모드: insanely-fast (GPU, 8002) - whisper large-v3
- 스트리밍: flm-server (NPU, 11434)

LLM 라우팅:
- 1순위: flm-server (NPU, 11434) - qwen3vl-it:4b
- 2순위: llama-server (GPU, 8080) - Router mode, 동적 모델 로드

OCR 라우팅:
- 신속모드: flm-server (NPU, 11434) - qwen3vl-it:4b
- 정확모드: llama-server (GPU, 8080) - Qwen3-VL-8B 동적 로드
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

# V6.6: Redis Stream GPU 클라이언트
from custom.gpu_stream_client import AsyncGPUStreamClient, get_async_gpu_stream_client

# V7.3: OpenTelemetry 분산 추적
try:
    from custom.telemetry import (
        trace_provider_call,
        trace_routing_decision,
        get_tracer,
        get_trace_id,
        ProviderAttributes,
    )
    TELEMETRY_ENABLED = True
except ImportError:
    TELEMETRY_ENABLED = False
    def trace_provider_call(*args, **kwargs):
        from contextlib import nullcontext
        return nullcontext()
    def trace_routing_decision(*args, **kwargs):
        from contextlib import nullcontext
        return nullcontext()
    def get_tracer(name): return None
    def get_trace_id(): return None

# Monkeypatch removed to avoid Enum validation error
# if not hasattr(litellm, "provider_list"):
#     litellm.provider_list = []
# if "prometheus-router" not in litellm.provider_list:
#     litellm.provider_list.append("prometheus-router")

logger = logging.getLogger(__name__)

# 환경변수
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://asr-prometheus:9090")
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-valkey:6379/0")

# Architecture V6.3: Provider Manager for On-Demand NPU control
PROVIDER_MANAGER_URL = os.getenv("PROVIDER_MANAGER_URL", "http://host.docker.internal:9999")

# Service Classification (V6.4 Simplified)
# GPU Services: Always-On - No start/stop control needed
GPU_SERVICES = {"llama", "whisper-cpp", "insanely-fast", "diarization-server"}
# NPU Services: On-Demand - Single FLM server for ASR + OCR
NPU_SERVICES = {"flm"}  # Single unified FLM server


# 기본값은 호스트 Docker 내부 주소 (LiteLLM 컨테이너 -> 호스트)
GPU_API_BASE = os.getenv("GPU_API_BASE", "http://host.docker.internal:8080")  # LLM
NPU_API_BASE = os.getenv("NPU_API_BASE", "http://host.docker.internal:11434")  # Unified FLM (ASR + OCR)

# 장치 ID - 동적 감지 사용 (LUID는 리부팅 시 변경될 수 있음)
# 디바이스 특성 기반 감지: NVIDIA GPU(3D만), AMD iGPU(3D+Video+Compute 혼합), AMD NPU(Compute만)
# get_gpu_device_ids_sync(), get_npu_device_ids_sync() 함수에서 동적으로 LUID를 감지함

# 캠시된 디바이스 ID (성능 최적화 - Prometheus 쿼리 최소화)
_cached_gpu_device_id: str | None = None
_cached_npu_device_id: str | None = None

# Chat/LLM 모델명 (V6.5)
GPU_MODEL = os.getenv("GPU_MODEL", "Qwen3-4B-Instruct-2507-Q4_K_S.gguf")  # llama-server Router mode
NPU_MODEL = os.getenv("NPU_MODEL", "qwen3vl-it:4b")  # FLM unified (LLM + OCR)

# ============================================================
# Tier-based Model Routing (공통 모듈에서 import)
# ============================================================
# infra/shared/tier_config.py에서 정의된 설정을 사용합니다.
# 모든 티어 관련 수정은 tier_config.py에서 하세요.
try:
    from infra.shared.tier_config import TIER_MODEL_MAP, resolve_tier_to_model, get_routing_policy
except ImportError:
    # Docker 환경에서 경로가 다를 수 있음
    import sys
    sys.path.insert(0, "/app/infra")
    from shared.tier_config import TIER_MODEL_MAP, resolve_tier_to_model, get_routing_policy

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
# V6.6: host.docker.internal HTTP 호출이 Docker Desktop 크래시를 유발
# Health check 비활성화하고 Redis Stream 응답으로 health 판단
HEALTH_CHECK_TIMEOUT = float(os.getenv("HEALTH_CHECK_TIMEOUT", "3.0"))  # 서버 응답 대기 시간
HEALTH_CHECK_ENABLED = os.getenv("HEALTH_CHECK_ENABLED", "false").lower() == "true"  # V6.6: 기본값 false

# V6.4: NPU OCR uses same FLM server as ASR (unified)
NPU_OCR_API_BASE = NPU_API_BASE  # Same port 11434

# Provider별 Health Check URL 매핑 (V6.4 Simplified)
PROVIDER_HEALTH_URLS = {
    "llama": f"{GPU_API_BASE}/health",
    "flm": f"{NPU_API_BASE}/v1/models",  # Unified FLM (ASR + OCR on 11434)
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


def get_gpu_device_ids_sync() -> list[str]:
    """Prometheus에서 GPU device ID를 동적으로 조회.

    디바이스 특성 기반 감지:
    - AMD iGPU: 3D + Video + Compute 혼합 (Video 엔진 있음)
    - AMD NPU: Compute 엔진만 보유 (Video 엔진 없음)

    Returns:
        GPU device ID 목록 (Video 엔진이 있는 디바이스)
    """
    import re
    from collections import defaultdict

    query = 'windows_gpu_engine_utilization_percentage'
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                # LUID별 엔진 타입 수집
                luid_engines: dict[str, set[str]] = defaultdict(set)

                for result in data["data"]["result"]:
                    metric = result["metric"]
                    exported_instance = metric.get("exported_instance", "")

                    # LUID 추출
                    luid_match = re.search(r'luid_0x[0-9A-Fa-f]+_0x([0-9A-Fa-f]+)', exported_instance)
                    if not luid_match:
                        continue
                    luid = '0x' + luid_match.group(1).lower()

                    # 엔진 타입 추출
                    engtype_match = re.search(r'engtype_([A-Za-z0-9_ ]+)', exported_instance)
                    if engtype_match:
                        engtype = engtype_match.group(1).strip()
                        luid_engines[luid].add(engtype)

                # GPU 판별: Video 엔진이 있는 디바이스 (AMD iGPU)
                gpus = []
                for luid, engines in luid_engines.items():
                    has_video = any('Video' in e for e in engines)

                    # AMD iGPU: Video 엔진이 있음 (실제 GPU는 Video 인코딩/디코딩 지원)
                    if has_video:
                        gpus.append(luid)
                        logger.debug(f"[Prometheus] Found GPU (Video engine): {luid} (engines: {engines})")

                if gpus:
                    return gpus

                # Fallback: 3D 엔진이 있는 모든 디바이스
                fallback_gpus = [luid for luid, engines in luid_engines.items()
                                 if any('3D' in e for e in engines)]
                if fallback_gpus:
                    logger.warning(f"[Prometheus] No GPU with Video engine, using fallback: {fallback_gpus}")
                return fallback_gpus

            return []
    except Exception as e:
        logger.warning(f"[Prometheus] Failed to get GPU device IDs: {e}")
        return []


def get_npu_device_ids_sync() -> list[str]:
    """Prometheus에서 Intel NPU device ID를 동적으로 조회.

    디바이스 특성 기반 감지:
    - Intel NPU: Compute 엔진만 보유 (3D/Video 엔진 없음)
    - Intel iGPU: 3D + Video + Compute 혼합
    - NVIDIA GPU: 3D 엔진만 보유

    Returns:
        Intel NPU device ID 목록 (예: ["0x000153C1"])
    """
    import re
    from collections import defaultdict

    # 1. AMD NPU exporter 메트릭 확인 (별도 exporter가 있는 경우)
    query_npu = 'count by (luid) (npu_total_gops)'
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query_npu}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                device_ids = []
                for result in data["data"]["result"]:
                    metric = result["metric"]
                    exported_instance = metric.get("exported_instance", "")
                    luid = exported_instance.split("luid_")[1].split("_")[0] if "luid_" in exported_instance else None
                    if luid and luid not in device_ids:
                        device_ids.append(luid)
                        logger.debug(f"[Prometheus] Found NPU device (AMD exporter): 0x{luid}")
                if device_ids:
                    return device_ids
    except Exception as e:
        logger.debug(f"[Prometheus] AMD NPU exporter query failed: {e}")

    # 2. windows_exporter에서 Intel NPU 동적 감지 (Compute 엔진만 있는 디바이스)
    query = 'windows_gpu_engine_utilization_percentage'
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                # LUID별 엔진 타입 수집
                luid_engines: dict[str, set[str]] = defaultdict(set)

                for result in data["data"]["result"]:
                    metric = result["metric"]
                    exported_instance = metric.get("exported_instance", "")

                    # LUID 추출
                    luid_match = re.search(r'luid_0x[0-9A-Fa-f]+_0x([0-9A-Fa-f]+)', exported_instance)
                    if not luid_match:
                        continue
                    luid = '0x' + luid_match.group(1).lower()

                    # 엔진 타입 추출
                    engtype_match = re.search(r'engtype_([A-Za-z0-9_ ]+)', exported_instance)
                    if engtype_match:
                        engtype = engtype_match.group(1).strip()
                        luid_engines[luid].add(engtype)

                # Intel NPU 판별: Compute 엔진만 있고 3D/Video 엔진이 없음
                intel_npus = []
                for luid, engines in luid_engines.items():
                    has_3d = any('3D' in e for e in engines)
                    has_video = any('Video' in e for e in engines)
                    has_compute = any('Compute' in e for e in engines)

                    # Intel NPU: Compute 엔진이 있고, 3D/Video 엔진이 없음
                    if has_compute and not has_3d and not has_video:
                        intel_npus.append(luid)
                        logger.debug(f"[Prometheus] Found Intel NPU: {luid} (engines: {engines})")

                if intel_npus:
                    return intel_npus

    except Exception as e:
        logger.debug(f"[Prometheus] GPU metrics query for NPU failed: {e}")

    return []


def query_single_device_sync(device_id: str, engine_filter: str = "non_compute") -> float:
    """Query Prometheus for GPU/NPU utilization.

    디바이스 특성 기반 동적 감지:
    - GPU: get_gpu_device_ids_sync()로 감지된 NVIDIA GPU LUID 사용
    - NPU: get_npu_device_ids_sync()로 감지된 AMD NPU LUID 사용

    Args:
        device_id: Unused, kept for compatibility
        engine_filter: "non_compute" for GPU (3D), "compute" for NPU

    Returns:
        Utilization percentage (0-100)
    """
    global _cached_gpu_device_id, _cached_npu_device_id

    if engine_filter == "non_compute":
        # GPU: 동적으로 감지된 NVIDIA GPU LUID 사용
        if _cached_gpu_device_id is None:
            gpu_ids = get_gpu_device_ids_sync()
            _cached_gpu_device_id = gpu_ids[0] if gpu_ids else ""
            logger.info(f"[Prometheus] Cached GPU device ID: {_cached_gpu_device_id}")
        if not _cached_gpu_device_id:
            return 0.0
        query = f'sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_gpu_device_id}.*engtype_3D.*"}})'
    else:
        # NPU: 동적으로 감지된 AMD NPU LUID 사용
        if _cached_npu_device_id is None:
            npu_ids = get_npu_device_ids_sync()
            _cached_npu_device_id = npu_ids[0] if npu_ids else ""
            logger.info(f"[Prometheus] Cached NPU device ID: {_cached_npu_device_id}")
        if not _cached_npu_device_id:
            return 0.0
        query = f'sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_npu_device_id}.*engtype_Compute.*"}})'

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                value = float(data["data"]["result"][0]["value"][1])
                logger.debug(f"[Prometheus] {engine_filter}: {value:.1f}%")
                return value
            return 0.0
    except Exception as e:
        logger.debug(f"[Prometheus] Query failed: {e}")
        return 0.0


def query_prometheus_sync(device_type: str = "gpu") -> float:
    """Prometheus에서 GPU/NPU 사용량을 동적으로 조회.

    Prometheus Metrics Reference:
    - GPU:  sum(windows_gpu_engine_utilization_percentage{engtype=~"3D|Video"})
           Windows 작업 관리자 GPU 사용률과 일치 (sum, 3D+Video only)
    - NPU:  max(windows_gpu_engine_utilization_percentage{engtype="Compute"})
           여러 Compute 엔진 중 가장 높은 사용률 (max)

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        0-100 사이의 사용량百分比
    """
    if device_type == "gpu":
        engine_filter = "non_compute"
    elif device_type == "npu":
        engine_filter = "compute"
    else:
        return 0.0

    return query_single_device_sync("", engine_filter)


async def query_memory_async(device_type: str = "gpu") -> dict:
    """Prometheus에서 GPU/NPU 메모리 사용량을 조회 (비동기).

    Prometheus Metrics Reference:
    - GPU: windows_gpu_dedicated_memory_usage_bytes
           windows_gpu_shared_memory_usage_bytes
    - NPU: windows_gpu_shared_memory_usage_bytes{engtype=Compute}
           windows_gpu_total_committed_bytes{engtype=Compute}

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        {"dedicated": bytes, "shared": bytes, "total": bytes, "percent": float}
    """
    if device_type == "gpu":
        query_parts = [
            'sum(windows_gpu_dedicated_memory_usage_bytes)',
            'sum(windows_gpu_shared_memory_usage_bytes)',
        ]
    elif device_type == "npu":
        query_parts = [
            'sum(windows_gpu_shared_memory_usage_bytes{exported_instance=~".*engtype_Compute.*"})',
            'sum(windows_gpu_total_committed_bytes{exported_instance=~".*engtype_Compute.*"})',
        ]
    else:
        return {"dedicated": 0, "shared": 0, "total": 0, "percent": 0.0}

    result = {"dedicated": 0, "shared": 0, "total": 0, "percent": 0.0}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for i, query in enumerate(query_parts):
                response = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": query}
                )
                response.raise_for_status()
                data = response.json()

                if data["status"] == "success" and data["data"]["result"]:
                    value = float(data["data"]["result"][0]["value"][1])
                    if i == 0:
                        result["dedicated"] = value
                    else:
                        result["shared"] = value

            result["total"] = result["dedicated"] + result["shared"]
            logger.debug(f"[Prometheus] Memory ({device_type}): dedicated={result['dedicated']/1e9:.1f}GB, shared={result['shared']/1e9:.1f}GB, total={result['total']/1e9:.1f}GB")

    except Exception as e:
        logger.debug(f"[Prometheus] Memory query failed for {device_type}: {e}")

    return result


def query_memory_sync(device_type: str = "gpu") -> dict:
    """Prometheus에서 GPU/NPU 메모리 사용량을 조회.

    Prometheus Metrics Reference:
    - GPU: windows_gpu_dedicated_memory_usage_bytes
           windows_gpu_shared_memory_usage_bytes
           (GPU device들의 메모리 합산)
    - NPU: windows_gpu_shared_memory_usage_bytes
           windows_gpu_total_committed_bytes
           (NPU device들의 메모리)

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        {"dedicated": bytes, "shared": bytes, "total": bytes, "percent": float}
    """
    if device_type == "gpu":
        query_parts = [
            'sum(windows_gpu_dedicated_memory_usage_bytes)',
            'sum(windows_gpu_shared_memory_usage_bytes)',
        ]
    elif device_type == "npu":
        query_parts = [
            'sum(windows_gpu_shared_memory_usage_bytes{exported_instance=~".*engtype_Compute.*"})',
            'sum(windows_gpu_total_committed_bytes{exported_instance=~".*engtype_Compute.*"})',
        ]
    else:
        return {"dedicated": 0, "shared": 0, "total": 0, "percent": 0.0}

    result = {"dedicated": 0, "shared": 0, "total": 0, "percent": 0.0}

    try:
        with httpx.Client(timeout=5.0) as client:
            # dedicated/shared memory
            for i, query in enumerate(query_parts):
                response = client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": query}
                )
                response.raise_for_status()
                data = response.json()

                if data["status"] == "success" and data["data"]["result"]:
                    value = float(data["data"]["result"][0]["value"][1])
                    if i == 0:
                        result["dedicated"] = value
                    else:
                        result["shared"] = value

            result["total"] = result["dedicated"] + result["shared"]
            logger.debug(f"[Prometheus] Memory ({device_type}): dedicated={result['dedicated']/1e9:.1f}GB, shared={result['shared']/1e9:.1f}GB, total={result['total']/1e9:.1f}GB")

    except Exception as e:
        logger.debug(f"[Prometheus] Memory query failed for {device_type}: {e}")

    return result


def format_bytes(b: float) -> str:
    """바이트를 읽기 쉬운 단위로 변환."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.1f}{unit}"
        b /= 1024.0
    return f"{b:.1f}PB"


async def query_single_device_async(device_id: str, engine_filter: str = "non_compute") -> float:
    """Query Prometheus for GPU/NPU utilization (async).

    디바이스 특성 기반 동적 감지:
    - GPU: get_gpu_device_ids_sync()로 감지된 NVIDIA GPU LUID 사용
    - NPU: get_npu_device_ids_sync()로 감지된 AMD NPU LUID 사용

    Args:
        device_id: Unused, kept for compatibility
        engine_filter: "non_compute" for GPU (3D), "compute" for NPU

    Returns:
        Utilization percentage (0-100)
    """
    global _cached_gpu_device_id, _cached_npu_device_id

    if engine_filter == "non_compute":
        # GPU: 동적으로 감지된 NVIDIA GPU LUID 사용
        if _cached_gpu_device_id is None:
            gpu_ids = get_gpu_device_ids_sync()
            _cached_gpu_device_id = gpu_ids[0] if gpu_ids else ""
            logger.info(f"[Prometheus] Cached GPU device ID: {_cached_gpu_device_id}")
        if not _cached_gpu_device_id:
            return 0.0
        query = f'sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_gpu_device_id}.*engtype_3D.*"}})'
    else:
        # NPU: 동적으로 감지된 AMD NPU LUID 사용
        if _cached_npu_device_id is None:
            npu_ids = get_npu_device_ids_sync()
            _cached_npu_device_id = npu_ids[0] if npu_ids else ""
            logger.info(f"[Prometheus] Cached NPU device ID: {_cached_npu_device_id}")
        if not _cached_npu_device_id:
            return 0.0
        query = f'sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_npu_device_id}.*engtype_Compute.*"}})'

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                value = float(data["data"]["result"][0]["value"][1])
                logger.debug(f"[Prometheus] Device {device_id} ({engine_filter}): {value:.1f}%")
                return value
            return 0.0
    except Exception as e:
        logger.debug(f"[Prometheus] Query failed for {device_id}: {e}")
        return 0.0


async def query_prometheus_async(device_type: str = "gpu") -> float:
    """Prometheus에서 GPU/NPU 사용량을 동적으로 조회 (비동기)."""
    device_ids = get_gpu_device_ids_sync()
    if not device_ids:
        return 0.0

    if device_type == "gpu":
        engine_filter = "non_compute"
    elif device_type == "npu":
        engine_filter = "compute"
    else:
        return 0.0

    total_usage = 0.0
    for device_id in device_ids:
        usage = await query_single_device_async(device_id, engine_filter)
        total_usage += usage

    logger.debug(f"[Prometheus] {device_type.upper()}: {total_usage:.1f}% (sum of {len(device_ids)} devices)")
    return total_usage


def check_provider_health_sync(provider: str) -> bool:
    """Provider 서버가 현재 실행 중인지 확인 (동기).

    Args:
        provider: 'llama', 'flm', 'whisper-cpp', 'insanely-fast', 'diarization-server'

    Returns:
        서버가 응답하면 True, 그렇지 않으면 False
    """
    if not HEALTH_CHECK_ENABLED:
        return True

    health_url = PROVIDER_HEALTH_URLS.get(provider)
    if not health_url:
        logger.warning(f"[HealthCheck] No health URL for provider: {provider}")
        return True

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
        return True

    health_url = PROVIDER_HEALTH_URLS.get(provider)
    if not health_url:
        logger.warning(f"[HealthCheck] No health URL for provider: {provider}")
        return True

    try:
        async with httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT) as client:
            response = await client.get(health_url)
            is_healthy = response.status_code == 200
            logger.debug(f"[HealthCheck] {provider}: {'healthy' if is_healthy else 'unhealthy'}")
            return is_healthy
    except Exception as e:
        logger.debug(f"[HealthCheck] {provider}: unreachable ({e})")
        return False


def check_flm_model_ready_sync(api_base: str, model: str, timeout: float = 10.0) -> bool:
    """FLM 서버 모델 로딩 완료 확인 (dry-run completion, 동기).

    FLM 서버는 /v1/models가 200을 반환해도 모델 로딩이 완료되지 않을 수 있음.
    간단한 completion 요청으로 실제 처리 가능 여부 확인.

    Args:
        api_base: FLM 서버 URL (예: http://host.docker.internal:11435)
        model: 모델 이름 (예: qwen3-it:4b)
        timeout: 요청 타임아웃 (초)

    Returns:
        모델이 실제로 요청 처리 가능하면 True
    """
    url = f"{api_base}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "what is 1+1"}],
        "max_tokens": 10,
        "stream": False,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            if response.status_code == 200:
                result = response.json()
                if result.get("choices"):
                    logger.debug(f"[CustomRouter] FLM model ready (dry-run success)")
                    return True
    except httpx.ReadTimeout:
        logger.debug(f"[CustomRouter] FLM dry-run timeout, model may still be loading")
    except Exception as e:
        logger.debug(f"[CustomRouter] FLM dry-run failed: {e}")

    return False


def check_flm_health_with_model_sync(provider: str) -> bool:
    """FLM Provider의 health + model readiness 확인 (동기).

    V6.5: 통합 FLM 서버 (11434) - ASR + OCR + LLM 통합

    Args:
        provider: 'flm' (통합 서버 11434)

    Returns:
        모델이 실제로 요청 처리 가능하면 True
    """
    if provider != "flm":
        return False

    api_base = NPU_API_BASE
    model = NPU_MODEL  # qwen3vl-it:4b

    if not check_provider_health_sync(provider):
        return False

    # V6.5: 통합 FLM 서버는 /v1/models 응답만으로 OK (dry-run 불필요)
    return True


async def check_flm_health_with_model_async(provider: str) -> bool:
    """FLM Provider의 health + model readiness 확인 (비동기).

    V6.5: 통합 FLM 서버 (11434) - ASR + OCR + LLM 통합

    Args:
        provider: 'flm' (통합 서버 11434)

    Returns:
        모델이 실제로 요청 처리 가능하면 True
    """
    if provider != "flm":
        return False

    api_base = NPU_API_BASE
    model = NPU_MODEL  # qwen3vl-it:4b

    if not await check_provider_health_async(provider):
        return False

    # V6.5: 통합 FLM 서버는 /v1/models 응답만으로 OK (dry-run 불필요)
    return True


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


async def is_provider_busy_async(provider: str) -> bool:
    """Provider busy 여부 확인 (Redis 세마포어만 사용).

    Args:
        provider: "npu" 또는 "gpu"

    Returns:
        busy 여부 (True면 사용 중)
    """
    if not redis_client_async:
        return False

    try:
        if provider == "npu":
            npu_active = await redis_client_async.exists("worker:npu:active")
            if npu_active:
                logger.debug(f"[BusyCheck] NPU busy (Redis semaphore)")
                return True
        elif provider == "gpu":
            gpu_busy, _ = await is_gpu_busy_async()
            if gpu_busy:
                logger.debug(f"[BusyCheck] GPU busy (Redis semaphore)")
                return True
    except Exception as e:
        logger.warning(f"[BusyCheck] Redis check failed: {e}")

    return False


def is_provider_busy_sync(provider: str) -> bool:
    """Provider busy 여부 확인 (Redis 세마포어만 사용) - 동기 버전.

    Args:
        provider: "npu" 또는 "gpu"

    Returns:
        busy 여부 (True면 사용 중)
    """
    if not redis_client_sync:
        return False

    try:
        if provider == "npu":
            npu_active = redis_client_sync.exists("worker:npu:active")
            if npu_active:
                logger.debug(f"[BusyCheck] NPU busy (Redis semaphore)")
                return True
        elif provider == "gpu":
            gpu_busy, _ = is_gpu_busy_sync()
            if gpu_busy:
                logger.debug(f"[BusyCheck] GPU busy (Redis semaphore)")
                return True
    except Exception as e:
        logger.warning(f"[BusyCheck] Redis check failed: {e}")

    return False


import asyncio


async def wait_for_available_provider_async(
    primary: str,
    fallback: str,
    task_type: str,
    get_provider_config_fn,
    max_wait: float = 3600.0,  # 1시간 (ASR/OCR 긴 작업 대응)
    poll_interval: float = 0.5,
) -> tuple[str, str, str, str]:
    """둘 다 busy일 때 대기하다가 먼저 available 되는 쪽 반환 (비동기).

    Args:
        primary: 우선 provider ("npu" 또는 "gpu")
        fallback: 대체 provider
        task_type: "chat" 또는 "audio"
        get_provider_config_fn: provider config 반환 함수
        max_wait: 최대 대기 시간 (초, 기본 1시간)
        poll_interval: 폴링 간격 (초, Redis 세마포어만 체크)

    Returns:
        (api_base, model, provider_key, signal_provider)

    Note:
        폴링 루프는 하나의 span으로 추적하되, 내부 Redis 호출은 트레이싱에서 제외 (노이즈 방지)
    """
    from opentelemetry import trace

    start_time = time.time()
    logger.info(f"[Wait] Both {primary.upper()} and {fallback.upper()} busy, waiting up to {max_wait:.0f}s...")

    # 폴링 루프 전체를 하나의 span으로 추적
    if TELEMETRY_ENABLED:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            "wait_for_available_provider",
            attributes={
                "primary": primary,
                "fallback": fallback,
                "task_type": task_type,
                "max_wait": max_wait,
            }
        ) as span:
            result = await _wait_for_available_provider_loop(
                primary, fallback, task_type, get_provider_config_fn,
                start_time, max_wait, poll_interval, span
            )
            return result
    else:
        return await _wait_for_available_provider_loop(
            primary, fallback, task_type, get_provider_config_fn,
            start_time, max_wait, poll_interval, None
        )


async def _wait_for_available_provider_loop(
    primary: str,
    fallback: str,
    task_type: str,
    get_provider_config_fn,
    start_time: float,
    max_wait: float,
    poll_interval: float,
    span=None,
) -> tuple[str, str, str, str]:
    """폴링 루프 내부 로직 (트레이싱 제외)."""
    from opentelemetry import trace

    while time.time() - start_time < max_wait:
        # 트레이싱 일시 중단 (Redis 호출 트레이스 제외)
        with trace.use_span(trace.INVALID_SPAN):
            # Primary 체크
            if not await is_provider_busy_async(primary):
                elapsed = time.time() - start_time
                if span:
                    span.set_attribute("wait_time_seconds", elapsed)
                    span.set_attribute("selected_provider", primary)
                logger.info(f"[Wait] {primary.upper()} available after {elapsed:.1f}s")
                return get_provider_config_fn(primary, task_type)

            # Fallback 체크
            if not await is_provider_busy_async(fallback):
                elapsed = time.time() - start_time
                if span:
                    span.set_attribute("wait_time_seconds", elapsed)
                    span.set_attribute("selected_provider", fallback)
                logger.info(f"[Wait] {fallback.upper()} available after {elapsed:.1f}s")
                return get_provider_config_fn(fallback, task_type)

        await asyncio.sleep(poll_interval)

    # Timeout → Primary 강제 반환 (retry 기대)
    if span:
        span.set_attribute("timeout", True)
        span.set_attribute("selected_provider", primary)
    logger.warning(f"[Wait] Timeout ({max_wait:.0f}s), forcing {primary.upper()}")
    return get_provider_config_fn(primary, task_type)


def wait_for_available_provider_sync(
    primary: str,
    fallback: str,
    task_type: str,
    get_provider_config_fn,
    max_wait: float = 3600.0,  # 1시간 (ASR/OCR 긴 작업 대응)
    poll_interval: float = 0.5,
) -> tuple[str, str, str, str]:
    """둘 다 busy일 때 대기하다가 먼저 available 되는 쪽 반환 (동기).

    Args:
        primary: 우선 provider ("npu" 또는 "gpu")
        fallback: 대체 provider
        task_type: "chat" 또는 "audio"
        get_provider_config_fn: provider config 반환 함수
        max_wait: 최대 대기 시간 (초, 기본 1시간)
        poll_interval: 폴링 간격 (초, Redis 세마포어만 체크)

    Returns:
        (api_base, model, provider_key, signal_provider)

    Note:
        폴링 루프는 하나의 span으로 추적하되, 내부 Redis 호출은 트레이싱에서 제외 (노이즈 방지)
    """
    from opentelemetry import trace

    start_time = time.time()
    logger.info(f"[Wait] Both {primary.upper()} and {fallback.upper()} busy, waiting up to {max_wait:.0f}s...")

    # 폴링 루프 전체를 하나의 span으로 추적
    if TELEMETRY_ENABLED:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            "wait_for_available_provider",
            attributes={
                "primary": primary,
                "fallback": fallback,
                "task_type": task_type,
                "max_wait": max_wait,
            }
        ) as span:
            result = _wait_for_available_provider_loop_sync(
                primary, fallback, task_type, get_provider_config_fn,
                start_time, max_wait, poll_interval, span
            )
            return result
    else:
        return _wait_for_available_provider_loop_sync(
            primary, fallback, task_type, get_provider_config_fn,
            start_time, max_wait, poll_interval, None
        )


def _wait_for_available_provider_loop_sync(
    primary: str,
    fallback: str,
    task_type: str,
    get_provider_config_fn,
    start_time: float,
    max_wait: float,
    poll_interval: float,
    span=None,
) -> tuple[str, str, str, str]:
    """폴링 루프 내부 로직 (트레이싱 제외) - 동기 버전."""
    from opentelemetry import trace

    while time.time() - start_time < max_wait:
        # 트레이싱 일시 중단 (Redis 호출 트레이스 제외)
        with trace.use_span(trace.INVALID_SPAN):
            # Primary 체크
            if not is_provider_busy_sync(primary):
                elapsed = time.time() - start_time
                if span:
                    span.set_attribute("wait_time_seconds", elapsed)
                    span.set_attribute("selected_provider", primary)
                logger.info(f"[Wait] {primary.upper()} available after {elapsed:.1f}s")
                return get_provider_config_fn(primary, task_type)

            # Fallback 체크
            if not is_provider_busy_sync(fallback):
                elapsed = time.time() - start_time
                if span:
                    span.set_attribute("wait_time_seconds", elapsed)
                    span.set_attribute("selected_provider", fallback)
                logger.info(f"[Wait] {fallback.upper()} available after {elapsed:.1f}s")
                return get_provider_config_fn(fallback, task_type)

        time.sleep(poll_interval)

    # Timeout → Primary 강제 반환 (retry 기대)
    if span:
        span.set_attribute("timeout", True)
        span.set_attribute("selected_provider", primary)
    logger.warning(f"[Wait] Timeout ({max_wait:.0f}s), forcing {primary.upper()}")
    return get_provider_config_fn(primary, task_type)


async def send_provider_control_signal(provider: str, action: str = "start"):
    """Host Agent에게 서비스 제어 요청 전송 (비동기, V6.3).

    Architecture V6.3:
    - GPU 서버: Always-On, 제어 신호 불필요 (no-op)
    - NPU 서버: On-Demand, Host Agent HTTP API로 제어

    Args:
        provider: 서비스 이름 (예: 'flm-llm-server' 또는 'flm-llm')
        action: 'start' or 'stop' (touch는 더 이상 필요 없음 - Servy가 health check로 관리)
    """
    # GPU 서버는 Always-On → 제어 신호 불필요
    if provider in GPU_SERVICES:
        logger.debug(f"[CustomRouter] GPU service '{provider}' is Always-On, skipping control signal")
        return

    # NPU 서비스 이름 정규화 (flm-llm → flm-llm-server)
    service_name = provider
    if provider in NPU_SERVICES:
        service_name = f"{provider}-server"

    # On-Demand: Host Agent HTTP API 호출
    if action == "start":
        url = f"{PROVIDER_MANAGER_URL}/start/{service_name}"
    elif action == "stop":
        url = f"{PROVIDER_MANAGER_URL}/stop/{service_name}"
    else:
        # touch는 V6.3에서 불필요 (Servy가 health check로 관리)
        logger.debug(f"[CustomRouter] Action '{action}' not supported in V6.3")
        return

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url)
            if response.status_code == 200:
                result = response.json()
                logger.info(f"[CustomRouter] Provider Manager: {service_name} -> {result.get('status', 'ok')}")
            else:
                logger.warning(f"[CustomRouter] Provider Manager error: {response.status_code} - {response.text}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Provider Manager request failed: {e}")


async def increment_active_count(provider: str):
    """Provider 활성 요청 카운트 증가 (비동기).

    V6.3 Note: 모니터링 용도로만 사용. Servy가 서비스 관리를 담당.
    """
    if not redis_client_async:
        return
    try:
        key = f"provider:{provider}:active_count"
        await redis_client_async.incr(key)
        logger.debug(f"[CustomRouter] INCR {key}")
    except Exception as e:
        logger.debug(f"[CustomRouter] Active count tracking skipped: {e}")


async def decrement_active_count(provider: str):
    """Provider 활성 요청 카운트 감소 (비동기).

    V6.3 Note: 모니터링 용도로만 사용. Servy가 서비스 관리를 담당.
    """
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
        logger.debug(f"[CustomRouter] Active count tracking skipped: {e}")


async def unload_llama_models():
    """llama-server에서 로드된 모델을 언로드하여 GPU 메모리 확보.

    ASR/Diarization 작업 전에 호출하여 GPU 메모리 충돌 방지.
    llama-server가 Router mode로 실행 중일 때만 동작.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. 로드된 모델 목록 조회
            resp = await client.get(f"{GPU_API_BASE}/models")
            if resp.status_code != 200:
                logger.debug(f"[CustomRouter] llama-server /models returned {resp.status_code}, skipping unload")
                return

            models_data = resp.json()
            models = models_data.get("data", [])

            if not models:
                logger.debug("[CustomRouter] No models loaded in llama-server")
                return

            # 2. 각 모델 언로드
            unloaded = []
            for model in models:
                model_id = model.get("id", "")
                if not model_id:
                    continue

                try:
                    unload_resp = await client.post(
                        f"{GPU_API_BASE}/models/unload",
                        json={"model": model_id}
                    )
                    if unload_resp.status_code == 200:
                        unloaded.append(model_id)
                    else:
                        logger.debug(f"[CustomRouter] Failed to unload {model_id}: {unload_resp.status_code}")
                except Exception as e:
                    logger.debug(f"[CustomRouter] Error unloading {model_id}: {e}")

            if unloaded:
                logger.info(f"[CustomRouter] Unloaded llama-server models for GPU memory: {unloaded}")

    except httpx.ConnectError:
        logger.debug("[CustomRouter] llama-server not running, skipping model unload")
    except Exception as e:
        logger.debug(f"[CustomRouter] Model unload skipped: {e}")


async def select_provider_async(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
    skip_signal: bool = False,
    tier: Optional[str] = None,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (비동기).

    Args:
        task_type: "chat" or "audio"
        force_provider: 강제 선택 - "gpu" or "npu" (None이면 자동 선택)
        skip_signal: Provider 시작 신호 전송 여부 (True면 호출자가 직접 전송)
        tier: 티어명 (예: "tier-simple", "tier-thinking") - 라우팅 정책 결정에 사용

    Returns:
        (api_base, model, provider_name)

    라우팅 우선순위 (하이브리드 방식):
    1. 강제 선택 (force_provider) → 해당 Provider 사용
    2. Tier별 정책 적용 → tier-thinking은 GPU 우선, tier-simple은 NPU 우선
    3. Primary busy → Fallback 시도
    4. 둘 다 busy → 대기 (최대 30초)
    5. 선택된 Provider unhealthy → start 신호 + 대기 (max 15초)
    """
    # Provider 설정 헬퍼 (V6.5: 통합 FLM 서버)
    def get_provider_config(provider: str, task: str) -> tuple[str, str, str, str]:
        """(api_base, model, provider_key, signal_provider)"""
        if provider == "npu":
            # V6.5: 모든 NPU 작업은 통합 FLM 서버 (11434) 사용
            if task == "audio":
                return NPU_API_BASE, NPU_AUDIO_MODEL, "npu-audio", "flm"
            return NPU_API_BASE, NPU_MODEL, "npu", "flm"  # LLM + OCR 통합
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
            await send_provider_control_signal("flm", "start")
        if task_type == "audio":
            return NPU_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_API_BASE, NPU_MODEL, "npu"  # V6.5: 통합 FLM (11434)

    # 1. Tier별 라우팅 정책 가져오기
    routing_policy = get_routing_policy(tier)
    policy_primary = routing_policy["primary"]
    policy_fallback = routing_policy["fallback"]
    queue_on_busy = routing_policy.get("queue_on_busy", True)

    logger.info(f"[CustomRouter] Tier: {tier or 'default'} → Policy: {policy_primary.upper()} primary, {policy_fallback.upper()} fallback")

    # 2. Primary/Fallback busy 상태 확인 (Redis 세마포어 + Prometheus)
    primary_busy = await is_provider_busy_async(policy_primary)
    fallback_busy = await is_provider_busy_async(policy_fallback)

    # 3. 라우팅 결정
    if not primary_busy:
        # Primary 사용 가능 → Primary 선택
        primary, fallback = policy_primary, policy_fallback
        logger.info(f"[CustomRouter] {policy_primary.upper()} available → Selected")
    elif not fallback_busy:
        # Primary busy, Fallback 사용 가능 → Fallback 선택
        primary, fallback = policy_fallback, policy_primary
        logger.info(f"[CustomRouter] {policy_primary.upper()} busy, {policy_fallback.upper()} available → Fallback")
    elif queue_on_busy:
        # 둘 다 busy → 대기
        logger.info(f"[CustomRouter] Both busy, waiting for available provider...")
        api_base, model, key, signal = await wait_for_available_provider_async(
            policy_primary, policy_fallback, task_type, get_provider_config
        )
        if not skip_signal:
            await send_provider_control_signal(signal, "start")
        return api_base, model, key
    else:
        # 대기 안 함 → Primary 강제 사용
        primary, fallback = policy_primary, policy_fallback
        logger.warning(f"[CustomRouter] Both busy, no queue → forcing {policy_primary.upper()}")

    # 4. Health Check
    # V6.5: 통합 FLM 서버 사용
    npu_health_provider = "flm"
    npu_healthy = await check_flm_health_with_model_async(npu_health_provider)
    gpu_healthy = await check_provider_health_async("llama")

    # 메모리 사용량 조회 (로깅용)
    gpu_avg = await query_prometheus_async("gpu")
    npu_avg = await query_prometheus_async("npu")
    gpu_mem = await query_memory_async("gpu")
    npu_mem = await query_memory_async("npu")

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}% ({format_bytes(gpu_mem['total'])}), NPU: {npu_avg:.1f}% ({format_bytes(npu_mem['total'])}) | Health - NPU({npu_health_provider}): {npu_healthy}, GPU: {gpu_healthy}")

    logger.info(f"[CustomRouter] Priority: {primary.upper()} (fallback: {fallback.upper()})")

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    if primary_healthy:
        # 이미 ready → 바로 사용
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (already healthy)")
        if not skip_signal:
            await send_provider_control_signal(primary_signal, "start")
        return primary_api, primary_model, primary_key

    # Primary Unhealthy Case
    # Check Fallback Health for Fast Failover
    fallback_api, fallback_model, fallback_key, fallback_signal = get_provider_config(fallback, task_type)
    fallback_healthy = gpu_healthy if fallback == "gpu" else npu_healthy

    if fallback_healthy:
        logger.warning(f"[CustomRouter] {primary.upper()} unhealthy but {fallback.upper()} is healthy. Fast Failover!")
        
        # [Background] Heal Primary (Send start signal)
        logger.info(f"[CustomRouter] Healing {primary.upper()} in background...")
        if not skip_signal:
            await send_provider_control_signal(primary_signal, "start")

        # [Immediate Action] Use Fallback
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (Fast Failover)")
        if not skip_signal:
            await send_provider_control_signal(fallback_signal, "start")
        return fallback_api, fallback_model, fallback_key

    # Both Unhealthy Case -> Try to start Primary with wait
    logger.info(f"[CustomRouter] Both providers unhealthy. Trying to start {primary.upper()}...")
    if not skip_signal:
        await send_provider_control_signal(primary_signal, "start")

    if await wait_for_server_ready_async(primary_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (started successfully)")
        return primary_api, primary_model, primary_key

    # 5. Fallback Provider 시도 (Primary Start 실패 시)
    logger.warning(f"[CustomRouter] {primary.upper()} failed to start, trying {fallback.upper()}...")
    
    # Fallback도 unhealthy (위에서 확인함) → start 신호 + 대기
    logger.info(f"[CustomRouter] {fallback.upper()} unhealthy, sending start signal...")
    if not skip_signal:
        await send_provider_control_signal(fallback_signal, "start")

    if await wait_for_server_ready_async(fallback_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (started successfully)")
        return fallback_api, fallback_model, fallback_key

    # 6. 둘 다 실패 → Primary 반환 (호출자가 에러 처리)
    logger.error(f"[CustomRouter] Both providers failed to start! Returning {primary.upper()} anyway...")
    return primary_api, primary_model, primary_key


def send_provider_control_signal_sync(provider: str, action: str = "start"):
    """Host Agent에게 서비스 제어 요청 전송 (동기, V6.3).

    Architecture V6.3:
    - GPU 서버: Always-On, 제어 신호 불필요 (no-op)
    - NPU 서버: On-Demand, Host Agent HTTP API로 제어
    """
    # GPU 서버는 Always-On → 제어 신호 불필요
    if provider in GPU_SERVICES:
        logger.debug(f"[CustomRouter] GPU service '{provider}' is Always-On, skipping control signal")
        return

    # NPU 서비스 이름 정규화 (flm-llm → flm-llm-server)
    service_name = provider
    if provider in NPU_SERVICES:
        service_name = f"{provider}-server"

    # On-Demand: Host Agent HTTP API 호출
    if action == "start":
        url = f"{PROVIDER_MANAGER_URL}/start/{service_name}"
    elif action == "stop":
        url = f"{PROVIDER_MANAGER_URL}/stop/{service_name}"
    else:
        # touch는 V6.3에서 불필요 (Servy가 health check로 관리)
        logger.debug(f"[CustomRouter] Action '{action}' not supported in V6.3")
        return

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url)
            if response.status_code == 200:
                result = response.json()
                logger.info(f"[CustomRouter] Provider Manager: {service_name} -> {result.get('status', 'ok')}")
            else:
                logger.warning(f"[CustomRouter] Provider Manager error: {response.status_code} - {response.text}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Provider Manager request failed: {e}")


def wait_for_server_ready_sync(api_base: str, max_wait: float = 60.0, interval: float = 1.0) -> bool:
    """서버가 준비될 때까지 대기 (동기).

    Args:
        api_base: 서버 기본 URL (예: http://host.docker.internal:11434)
        max_wait: 최대 대기 시간 (초)
        interval: 체크 간격 (초)

    Returns:
        서버 준비 완료 여부
    """
    # FLM 서버 여부 판별
    is_flm_server = ":11434" in api_base  # V6.5: 통합 FLM 서버만

    # Health endpoint 결정
    if is_flm_server:
        # FLM 서버: /v1/models로 기본 체크
        health_url = f"{api_base}/v1/models"
    elif ":8080" in api_base:
        # llama-server: /health 엔드포인트
        health_url = f"{api_base}/health"
    else:
        health_url = f"{api_base}/health"

    start_time = time.time()
    attempt = 0
    health_passed = False

    while (time.time() - start_time) < max_wait:
        attempt += 1
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(health_url)
                if response.status_code == 200:
                    if not health_passed:
                        elapsed = time.time() - start_time
                        logger.info(f"[CustomRouter] Server responding at {api_base} after {elapsed:.1f}s (attempt {attempt})")
                        health_passed = True

                    # V6.5: 통합 FLM 서버는 /v1/models 응답만으로 OK
                    if is_flm_server:
                        elapsed = time.time() - start_time
                        logger.info(f"[CustomRouter] FLM server ready at {api_base} after {elapsed:.1f}s")
                        return True
                    else:
                        # 비-FLM 서버는 health check만으로 OK
                        return True
        except Exception as e:
            if attempt == 1:
                logger.info(f"[CustomRouter] Waiting for server at {api_base}...")
            logger.debug(f"[CustomRouter] Server not ready (attempt {attempt}): {e}")

        time.sleep(interval)

    logger.warning(f"[CustomRouter] Server at {api_base} not ready after {max_wait}s")
    return False


async def check_flm_model_ready_async(api_base: str, model: str, timeout: float = 10.0) -> bool:
    """FLM 서버 모델 로딩 완료 확인 (dry-run completion).

    FLM 서버는 /v1/models가 200을 반환해도 모델 로딩이 완료되지 않을 수 있음.
    간단한 completion 요청으로 실제 처리 가능 여부 확인.

    Args:
        api_base: FLM 서버 URL (예: http://host.docker.internal:11435)
        model: 모델 이름 (예: qwen3-it:4b)
        timeout: 요청 타임아웃 (초)

    Returns:
        모델이 실제로 요청 처리 가능하면 True
    """
    url = f"{api_base}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "what is 1+1"}],
        "max_tokens": 10,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                result = response.json()
                # 응답에 choices가 있으면 모델 로딩 완료
                if result.get("choices"):
                    logger.debug(f"[CustomRouter] FLM model ready (dry-run success)")
                    return True
    except httpx.ReadTimeout:
        # 타임아웃은 모델이 아직 로딩 중일 수 있음
        logger.debug(f"[CustomRouter] FLM dry-run timeout, model may still be loading")
    except Exception as e:
        logger.debug(f"[CustomRouter] FLM dry-run failed: {e}")

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

    # FLM 서버 여부 판별
    is_flm_server = ":11434" in api_base  # V6.5: 통합 FLM 서버만

    # Health endpoint 결정
    if is_flm_server:
        # FLM 서버: /v1/models로 기본 체크
        health_url = f"{api_base}/v1/models"
    elif ":8080" in api_base:
        # llama-server: /health 엔드포인트
        health_url = f"{api_base}/health"
    else:
        health_url = f"{api_base}/health"

    start_time = time.time()
    attempt = 0
    health_passed = False

    while (time.time() - start_time) < max_wait:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(health_url)
                if response.status_code == 200:
                    if not health_passed:
                        elapsed = time.time() - start_time
                        logger.info(f"[CustomRouter] Server responding at {api_base} after {elapsed:.1f}s (attempt {attempt})")
                        health_passed = True

                    # V6.5: 통합 FLM 서버는 /v1/models 응답만으로 OK
                    if is_flm_server:
                        elapsed = time.time() - start_time
                        logger.info(f"[CustomRouter] FLM server ready at {api_base} after {elapsed:.1f}s")
                        return True
                    else:
                        # 비-FLM 서버는 health check만으로 OK
                        return True
        except Exception as e:
            if attempt == 1:
                logger.info(f"[CustomRouter] Waiting for server at {api_base}...")
            logger.debug(f"[CustomRouter] Server not ready (attempt {attempt}): {e}")

        await asyncio.sleep(interval)

    logger.warning(f"[CustomRouter] Server at {api_base} not ready after {max_wait}s")
    return False


def increment_active_count_sync(provider: str):
    """Provider 활성 요청 카운트 증가 (동기).

    V6.3 Note: 모니터링 용도로만 사용. Servy가 서비스 관리를 담당.
    """
    if not redis_client_sync:
        return
    try:
        key = f"provider:{provider}:active_count"
        redis_client_sync.incr(key)
        logger.debug(f"[CustomRouter] INCR {key}")
    except Exception as e:
        logger.debug(f"[CustomRouter] Active count tracking skipped: {e}")


def decrement_active_count_sync(provider: str):
    """Provider 활성 요청 카운트 감소 (동기).

    V6.3 Note: 모니터링 용도로만 사용. Servy가 서비스 관리를 담당.
    """
    if not redis_client_sync:
        return
    try:
        key = f"provider:{provider}:active_count"
        new_val = redis_client_sync.decr(key)
        if new_val < 0:
            redis_client_sync.set(key, 0)
        logger.debug(f"[CustomRouter] DECR {key} -> {max(0, new_val)}")
    except Exception as e:
        logger.debug(f"[CustomRouter] Active count tracking skipped: {e}")


def select_provider_sync(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
    skip_signal: bool = False,
    tier: Optional[str] = None,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (동기).

    Args:
        task_type: "chat" or "audio"
        force_provider: 강제 선택 - "gpu" or "npu" (None이면 자동 선택)
        skip_signal: Provider 시작 신호 전송 여부 (True면 호출자가 직접 전송)
        tier: 티어명 (예: "tier-simple", "tier-thinking") - 라우팅 정책 결정에 사용

    Returns:
        (api_base, model, provider_name)

    라우팅 우선순위 (하이브리드 방식):
    1. 강제 선택 (force_provider) → 해당 Provider 사용
    2. Tier별 정책 적용 → tier-thinking은 GPU 우선, tier-simple은 NPU 우선
    3. Primary busy → Fallback 시도
    4. 둘 다 busy → 대기 (최대 30초)
    5. 선택된 Provider unhealthy → start 신호 + 대기 (max 15초)
    """
    # Provider 설정 헬퍼 (V6.5: 통합 FLM 서버)
    def get_provider_config(provider: str, task: str) -> tuple[str, str, str, str]:
        """(api_base, model, provider_key, signal_provider)"""
        if provider == "npu":
            # V6.5: 모든 NPU 작업은 통합 FLM 서버 (11434) 사용
            if task == "audio":
                return NPU_API_BASE, NPU_AUDIO_MODEL, "npu-audio", "flm"
            return NPU_API_BASE, NPU_MODEL, "npu", "flm"  # LLM + OCR 통합
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
            send_provider_control_signal_sync("flm", "start")
        if task_type == "audio":
            return NPU_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_API_BASE, NPU_MODEL, "npu"  # V6.5: 통합 FLM (11434)

    # 1. Tier별 라우팅 정책 가져오기
    routing_policy = get_routing_policy(tier)
    policy_primary = routing_policy["primary"]
    policy_fallback = routing_policy["fallback"]
    queue_on_busy = routing_policy.get("queue_on_busy", True)

    logger.info(f"[CustomRouter] Tier: {tier or 'default'} → Policy: {policy_primary.upper()} primary, {policy_fallback.upper()} fallback")

    # 2. Primary/Fallback busy 상태 확인 (Redis 세마포어 + Prometheus)
    primary_busy = is_provider_busy_sync(policy_primary)
    fallback_busy = is_provider_busy_sync(policy_fallback)

    # 3. 라우팅 결정
    if not primary_busy:
        # Primary 사용 가능 → Primary 선택
        primary, fallback = policy_primary, policy_fallback
        logger.info(f"[CustomRouter] {policy_primary.upper()} available → Selected")
    elif not fallback_busy:
        # Primary busy, Fallback 사용 가능 → Fallback 선택
        primary, fallback = policy_fallback, policy_primary
        logger.info(f"[CustomRouter] {policy_primary.upper()} busy, {policy_fallback.upper()} available → Fallback")
    elif queue_on_busy:
        # 둘 다 busy → 대기
        logger.info(f"[CustomRouter] Both busy, waiting for available provider...")
        api_base, model, key, signal = wait_for_available_provider_sync(
            policy_primary, policy_fallback, task_type, get_provider_config
        )
        if not skip_signal:
            send_provider_control_signal_sync(signal, "start")
        return api_base, model, key
    else:
        # 대기 안 함 → Primary 강제 사용
        primary, fallback = policy_primary, policy_fallback
        logger.warning(f"[CustomRouter] Both busy, no queue → forcing {policy_primary.upper()}")

    # 4. Health Check
    # V6.5: 통합 FLM 서버 사용
    npu_health_provider = "flm"
    npu_healthy = check_flm_health_with_model_sync(npu_health_provider)
    gpu_healthy = check_provider_health_sync("llama")

    # 메모리 사용량 조회 (로깅용)
    gpu_avg = query_prometheus_sync("gpu")
    npu_avg = query_prometheus_sync("npu")
    gpu_mem = query_memory_sync("gpu")
    npu_mem = query_memory_sync("npu")

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}% ({format_bytes(gpu_mem['total'])}), NPU: {npu_avg:.1f}% ({format_bytes(npu_mem['total'])}) | Health - NPU({npu_health_provider}): {npu_healthy}, GPU: {gpu_healthy}")

    logger.info(f"[CustomRouter] Priority: {primary.upper()} (fallback: {fallback.upper()})")

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    # 4. Primary Provider 시도
    primary_api, primary_model, primary_key, primary_signal = get_provider_config(primary, task_type)
    primary_healthy = npu_healthy if primary == "npu" else gpu_healthy

    if primary_healthy:
        # 이미 ready → 바로 사용
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (already healthy)")
        if not skip_signal:
            send_provider_control_signal_sync(primary_signal, "start")
        return primary_api, primary_model, primary_key

    # Primary Unhealthy Case
    # Check Fallback Health for Fast Failover
    fallback_api, fallback_model, fallback_key, fallback_signal = get_provider_config(fallback, task_type)
    fallback_healthy = gpu_healthy if fallback == "gpu" else npu_healthy

    if fallback_healthy:
        logger.warning(f"[CustomRouter] {primary.upper()} unhealthy but {fallback.upper()} is healthy. Fast Failover!")
        
        # [Background] Heal Primary (Send start signal)
        logger.info(f"[CustomRouter] Healing {primary.upper()} in background...")
        if not skip_signal:
            send_provider_control_signal_sync(primary_signal, "start")

        # [Immediate Action] Use Fallback
        logger.info(f"[CustomRouter] Selected: {fallback.upper()} (Fast Failover)")
        if not skip_signal:
            send_provider_control_signal_sync(fallback_signal, "start")
        return fallback_api, fallback_model, fallback_key

    # Both Unhealthy Case -> Try to start Primary with wait
    logger.info(f"[CustomRouter] Both providers unhealthy. Trying to start {primary.upper()}...")
    if not skip_signal:
        send_provider_control_signal_sync(primary_signal, "start")

    if wait_for_server_ready_sync(primary_api, max_wait=15.0, interval=1.0):
        logger.info(f"[CustomRouter] Selected: {primary.upper()} (started successfully)")
        return primary_api, primary_model, primary_key

    # 5. Fallback Provider 시도 (Primary Start 실패 시)
    logger.warning(f"[CustomRouter] {primary.upper()} failed to start, trying {fallback.upper()}...")
    
    # Fallback도 unhealthy (위에서 확인함) → start 신호 + 대기
    logger.info(f"[CustomRouter] {fallback.upper()} unhealthy, sending start signal...")
    if not skip_signal:
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
    
    def _is_vision_request(self, messages: list) -> tuple[bool, Optional[str], Optional[str]]:
        """메시지에 이미지가 포함되어 있는지 확인.

        Returns:
            (is_vision, image_base64, text_prompt)
        """
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                image_base64 = None
                text_prompt = ""
                for item in content:
                    if item.get("type") == "image_url":
                        image_url = item.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image"):
                            # data:image/jpeg;base64,... 형식
                            image_base64 = image_url.split(",", 1)[1] if "," in image_url else None
                    elif item.get("type") == "text":
                        text_prompt = item.get("text", "")
                if image_base64:
                    return True, image_base64, text_prompt
        return False, None, None

    def completion(self, *args, **kwargs) -> ModelResponse:
        """동기 completion (Non-streaming) - V7.0 Redis Stream 기반.

        Architecture V7.0:
        - HTTP 직접 통신 대신 Redis Stream을 통해 Provider Manager로 요청
        - Vision/OCR 요청 감지 및 처리
        - Docker Desktop 크래시 방지
        """
        from custom.gpu_stream_client import get_gpu_stream_client
        import base64

        messages = kwargs.get("messages", [])
        requested_model = kwargs.get("model", "")

        litellm_params = kwargs.get("litellm_params", {})
        optional_params = kwargs.get("optional_params", {})
        stream = kwargs.get("stream", False) or litellm_params.get("stream") or optional_params.get("stream")

        logger.info(f"[PrometheusRouter V7.0] completion called. model={requested_model}, stream={stream}")
        if stream:
            logger.warning("[PrometheusRouter V7.0] stream=True detected in completion. LiteLLM should have called astreaming.")

        # V7.0: OCR 요청 감지 (모델명이 ocr-로 시작하거나 vision 요청)
        # 라우터 prefix 제거 (예: "prometheus-router/ocr-speed" -> "ocr-speed")
        model_name = requested_model.split("/")[-1] if "/" in requested_model else requested_model
        is_ocr_model = model_name.startswith("ocr-")
        is_vision, image_base64, text_prompt = self._is_vision_request(messages)

        if is_ocr_model or is_vision:
            logger.info(f"[PrometheusRouter V7.0] OCR/Vision request detected: model={requested_model} (extracted: {model_name})")

            # accuracy_mode 추출 (extra_body에서)
            extra_body = optional_params.get("extra_body", {}) or kwargs.get("extra_body", {})
            accuracy_mode = extra_body.get("accuracy_mode", "speed")

            # OCR 모델 결정
            if accuracy_mode == "speed":
                ocr_model = "qwen3vl-it:4b"  # FLM NPU
                target_provider = "flm"
            else:
                ocr_model = "qwen3-vl-8b"  # GPU llama-ocr-server
                target_provider = "llama-ocr"

            increment_active_count_sync(target_provider)

            try:
                gpu_client = get_gpu_stream_client()

                if image_base64:
                    # V7.0: Redis Stream을 통한 OCR 요청
                    # V7.3: trace_id 로깅 추가
                    trace_id = get_trace_id() if TELEMETRY_ENABLED else None
                    if trace_id:
                        logger.info(f"[PrometheusRouter V7.3] OCR request trace_id={trace_id}")

                    result = gpu_client.request_ocr(
                        image_base64=image_base64,
                        model=ocr_model,
                        prompt=text_prompt or "Extract all text from this image.",
                        accuracy_mode=accuracy_mode,
                        timeout=300.0,
                    )

                    # OCR 결과를 OpenAI 응답 형식으로 변환
                    ocr_text = result.get("text", "")
                    if not ocr_text and "choices" in result:
                        # LLM 응답 형식인 경우
                        ocr_text = result["choices"][0].get("message", {}).get("content", "")

                    return ModelResponse(
                        id=result.get("id", f"chatcmpl-{uuid.uuid4()}"),
                        created=result.get("created", int(time.time())),
                        model=ocr_model,
                        object="chat.completion",
                        choices=[
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": ocr_text,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        usage=result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
                    )
                else:
                    raise ValueError("No image data provided for OCR request")
            except Exception as e:
                logger.error(f"[PrometheusRouter V7.0] OCR completion failed: {e}")
                raise
            finally:
                decrement_active_count_sync(target_provider)

        # 일반 LLM 요청
        # Tier 정보 추출 (model_name이 tier-로 시작하면 해당 tier 사용)
        tier = model_name if model_name.startswith("tier-") else None
        # Provider 선택 (신호 없이 - Redis Stream이 처리)
        api_base, _, provider_key = select_provider_sync(task_type="chat", skip_signal=True, tier=tier)
        target_provider = "flm" if "npu" in provider_key else "llama"

        # 요청된 모델명 사용 (라우터 prefix 제거)
        requested_model_name = model_name  # 1478행에서 추출됨

        logger.info(f"[PrometheusRouter V7.0] Routing to {target_provider} via Redis Stream, model={requested_model_name}")

        # 활성 요청 카운트 증가
        increment_active_count_sync(target_provider)

        try:
            # Redis Stream을 통한 LLM 요청 (동기)
            gpu_client = get_gpu_stream_client()
            result = gpu_client.request_llm_completion(
                messages=messages,
                model=requested_model_name,
                max_tokens=optional_params.get("max_tokens", 4096),
                temperature=optional_params.get("temperature", 0.7),
                target_server=target_server,
                timeout=120.0,
            )

            return ModelResponse(
                id=result.get("id", f"chatcmpl-{uuid.uuid4()}"),
                created=result.get("created", int(time.time())),
                model=result.get("model", requested_model_name),
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
        except Exception as e:
            logger.error(f"[PrometheusRouter V7.0] LLM completion failed: {e}")
            raise
        finally:
            decrement_active_count_sync(target_provider)

    async def acompletion(self, *args, **kwargs) -> ModelResponse:
        """비동기 completion (Non-streaming) - V7.0 Redis Stream 기반."""
        import base64

        messages = kwargs.get("messages", [])
        requested_model = kwargs.get("model", "")
        optional_params = kwargs.get("optional_params", {})

        logger.info(f"[PrometheusRouter V7.0] acompletion called. model={requested_model}")

        # V7.0: OCR 요청 감지 (모델명이 ocr-로 시작하거나 vision 요청)
        # 라우터 prefix 제거 (예: "prometheus-router/ocr-speed" -> "ocr-speed")
        model_name = requested_model.split("/")[-1] if "/" in requested_model else requested_model
        is_ocr_model = model_name.startswith("ocr-")
        is_vision, image_base64, text_prompt = self._is_vision_request(messages)

        if is_ocr_model or is_vision:
            logger.info(f"[PrometheusRouter V7.0] OCR/Vision request detected (async): model={requested_model} (extracted: {model_name})")

            # accuracy_mode 추출 (extra_body에서)
            extra_body = optional_params.get("extra_body", {}) or kwargs.get("extra_body", {})
            accuracy_mode = extra_body.get("accuracy_mode", "speed")

            # OCR 모델 결정
            if accuracy_mode == "speed":
                ocr_model = "qwen3vl-it:4b"  # FLM NPU
                target_provider = "flm"
            else:
                ocr_model = "qwen3-vl-8b"  # GPU llama-ocr-server
                target_provider = "llama-ocr"

            await increment_active_count(target_provider)

            try:
                gpu_client = get_async_gpu_stream_client()

                if image_base64:
                    # V7.0: Redis Stream을 통한 OCR 요청 (비동기)
                    image_data = base64.b64decode(image_base64)
                    result = await gpu_client.request_ocr(
                        image_data=image_data,
                        model=ocr_model,
                        prompt=text_prompt or "Extract all text from this image.",
                        accuracy_mode=accuracy_mode,
                        timeout=300.0,
                    )

                    # OCR 결과를 OpenAI 응답 형식으로 변환
                    ocr_text = result.get("text", "")
                    if not ocr_text and "choices" in result:
                        # LLM 응답 형식인 경우
                        ocr_text = result["choices"][0].get("message", {}).get("content", "")

                    return ModelResponse(
                        id=result.get("id", f"chatcmpl-{uuid.uuid4()}"),
                        created=result.get("created", int(time.time())),
                        model=ocr_model,
                        object="chat.completion",
                        choices=[
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": ocr_text,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        usage=result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
                    )
                else:
                    raise ValueError("No image data provided for OCR request")
            except Exception as e:
                logger.error(f"[PrometheusRouter V7.0] OCR acompletion failed: {e}")
                raise
            finally:
                await decrement_active_count(target_provider)

        # 일반 LLM 요청
        # Tier 정보 추출 (model_name이 tier-로 시작하면 해당 tier 사용)
        tier = model_name if model_name.startswith("tier-") else None
        # Provider 선택 (신호 없이 - Redis Stream이 처리)
        api_base, _, provider_key = await select_provider_async(task_type="chat", skip_signal=True, tier=tier)
        target_provider = "flm" if "npu" in provider_key else "llama"

        # 요청된 모델명 사용 (라우터 prefix 제거)
        requested_model_name = model_name  # 1607행에서 추출됨

        logger.info(f"[PrometheusRouter V7.0] Routing to {target_provider} via Redis Stream, model={requested_model_name}, tier={tier}")

        # 활성 요청 카운트 증가
        await increment_active_count(target_provider)

        try:
            # Redis Stream을 통한 LLM 요청 (비동기)
            gpu_client = get_async_gpu_stream_client()
            result = await gpu_client.request_llm_completion(
                messages=messages,
                model=requested_model_name,
                max_tokens=optional_params.get("max_tokens", 4096),
                temperature=optional_params.get("temperature", 0.7),
                target_server=target_provider,
                timeout=600.0,
            )

            return ModelResponse(
                id=result.get("id", f"chatcmpl-{uuid.uuid4()}"),
                created=result.get("created", int(time.time())),
                model=result.get("model", requested_model_name),
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
        except Exception as e:
            logger.error(f"[PrometheusRouter V7.0] acompletion failed: {e}")
            raise
        finally:
            await decrement_active_count(target_provider)

    async def astreaming(self, *args, **kwargs) -> AsyncIterator[GenericStreamingChunk]:
        """비동기 스트리밍 - V7.4 Redis Stream 기반 (실시간 스트리밍)."""
        start_ts = time.time()

        # model parameter extraction for logging
        model = kwargs.get("model", "")
        if not model:
            litellm_params = kwargs.get("litellm_params", {})
            model = litellm_params.get("model", "unknown")

        litellm_params = kwargs.get("litellm_params", {})
        metadata = litellm_params.get("metadata", {}) or {}
        trace_id = metadata.get("trace_id", "unknown")
        optional_params = kwargs.get("optional_params", {})

        def get_log_prefix():
            return f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}][{trace_id}]"

        logger.info(f"{get_log_prefix()} [PrometheusRouter V7.4] Streaming Request START: {model}")

        messages = kwargs.get("messages", [])

        # 요청된 모델명 추출 (라우터 prefix 제거)
        raw_model_name = model.split("/")[-1] if "/" in model else model

        # Tier 정보 추출 (model_name이 tier-로 시작하면 해당 tier 사용)
        tier = raw_model_name if raw_model_name.startswith("tier-") else None

        # Provider 선택 (신호 없이 - Redis Stream이 처리, tier별 정책 적용)
        api_base, _, provider_key = await select_provider_async(task_type="chat", skip_signal=True, tier=tier)
        target_provider = "flm" if "npu" in provider_key else "llama"

        # V8.0: Tier-based routing - 티어명을 실제 모델명으로 변환
        requested_model_name = resolve_tier_to_model(raw_model_name)

        selection_latency = time.time() - start_ts
        logger.debug(f"{get_log_prefix()} [PrometheusRouter V8.0] Provider: {target_provider}, model={requested_model_name}, tier={tier} (Latency: {selection_latency:.3f}s)")

        await increment_active_count(target_provider)

        try:
            # Redis Stream을 통한 LLM 요청 (비동기, Real-time streaming)
            gpu_client = get_async_gpu_stream_client()

            stream_gen = gpu_client.request_llm_completion_stream(
                messages=messages,
                model=requested_model_name,
                max_tokens=optional_params.get("max_tokens", 4096),
                temperature=optional_params.get("temperature", 0.7),
                target_server=target_provider,
                timeout=600.0,  # 추론 모드의 긴 TTFB 대응 (10분)
            )

            ttfb_received = False
            
            async for chunk_data in stream_gen:
                if not ttfb_received:
                    ttfb_latency = time.time() - start_ts
                    logger.info(f"{get_log_prefix()} [PrometheusRouter V7.4] First chunk received (TTFB: {ttfb_latency:.3f}s)")
                    ttfb_received = True

                if "chunk" in chunk_data:
                    yield GenericStreamingChunk(
                        text=chunk_data["chunk"],
                        is_finished=False,
                        finish_reason=None,
                        usage=None,
                    )
                
                if "result" in chunk_data:
                    # Final result (usage, finish_reason)
                    result = chunk_data["result"]
                    yield GenericStreamingChunk(
                        text="",
                        is_finished=True,
                        finish_reason=result.get("choices", [{}])[0].get("finish_reason", "stop"),
                        usage=result.get("usage"),
                    )

            total_duration = time.time() - start_ts
            logger.info(f"{get_log_prefix()} [PrometheusRouter V7.4] Request END: {model} (Total: {total_duration:.3f}s)")

        except Exception as e:
            total_duration = time.time() - start_ts
            logger.error(f"{get_log_prefix()} [PrometheusRouter V7.4] Request FAILED: {model} (Total: {total_duration:.3f}s, Error: {e})")
            raise
        finally:
            await decrement_active_count(target_provider)

    async def transcription(self, *args, **kwargs) -> ModelResponse:
        """Audio Transcription - V6.6 Redis Stream 기반.

        Architecture V6.6:
        - HTTP 직접 통신 대신 Redis Stream을 통해 Provider Manager로 요청
        - Provider Manager가 Host에서 localhost로 GPU 서버 접근
        - Docker Desktop 크래시 방지
        """
        from pathlib import Path
        import tempfile

        # Prepare multipart/form-data
        files = kwargs.get("files", {})
        data = kwargs.get("data", {})
        requested_model = data.get("model", "")

        # GPU Stream Client 가져오기
        gpu_client = get_async_gpu_stream_client()

        # 파일 데이터 추출 및 임시 파일로 저장
        file_tuple = files.get("file")
        if not file_tuple:
            raise ValueError("No audio file provided")

        # file_tuple: (filename, file_object, content_type) 또는 file object
        if isinstance(file_tuple, tuple):
            filename, file_obj, _ = file_tuple
        else:
            file_obj = file_tuple
            filename = getattr(file_obj, 'name', 'audio.wav')

        # 임시 파일로 저장 (Redis Stream은 파일 경로 필요)
        temp_dir = Path(tempfile.gettempdir())
        temp_file = temp_dir / f"litellm_audio_{uuid.uuid4().hex[:8]}.wav"

        try:
            if hasattr(file_obj, 'read'):
                file_obj.seek(0)
                content = file_obj.read()
                temp_file.write_bytes(content)
            else:
                temp_file.write_bytes(file_obj)

            # 0. Diarization 라우팅 (model="pyannote")
            if requested_model.endswith("pyannote"):
                logger.info(f"[PrometheusRouter V6.6] Routing to Diarization via Redis Stream: model={requested_model}")

                # 활성 카운트 증가 (모니터링용)
                await increment_active_count("diarization-server")

                try:
                    # Redis Stream을 통한 Diarization 요청
                    min_speakers = data.get("min_speakers")
                    max_speakers = data.get("max_speakers")

                    result = await gpu_client.request_diarization(
                        audio_file_path=temp_file,
                        min_speakers=int(min_speakers) if min_speakers else None,
                        max_speakers=int(max_speakers) if max_speakers else None,
                        timeout=1800.0,
                    )

                    logger.info(f"[PrometheusRouter V6.6] Diarization completed via Redis Stream")
                    return result
                except Exception as e:
                    logger.error(f"[PrometheusRouter V6.6] Diarization Error: {e}")
                    raise e
                finally:
                    await decrement_active_count("diarization-server")

            # 모드 판별 (라우터 prefix 제거)
            model_name = requested_model.split("/")[-1] if "/" in requested_model else requested_model
            is_accuracy_mode = model_name in ("whisper-large-v3", "whisper", "openai/whisper-large-v3")
            is_turbo_mode = model_name in ("whisper-turbo", "whisper-large-v3-turbo", "turbo", "openai/whisper-turbo")
            is_speed_mode = model_name in ("flm-audio", "flm", "openai/flm-audio")

            # 1. 라우팅 대상 모델 결정
            if is_accuracy_mode:
                target_model = "whisper-large-v3"
                target_provider = "insanely-fast"
                logger.info(f"[PrometheusRouter V6.6] Model '{requested_model}' -> GPU Accuracy (Insanely-Fast)")
            elif is_turbo_mode:
                target_model = "whisper-turbo"
                target_provider = "whisper-cpp"
                logger.info(f"[PrometheusRouter V6.6] Model '{requested_model}' -> GPU Speed (Whisper.cpp)")
            elif is_speed_mode:
                target_model = "flm-audio"
                target_provider = "flm"
                logger.info(f"[PrometheusRouter V6.6] Model '{requested_model}' -> NPU Speed (FLM)")
            else:
                target_model = "whisper-large-v3"
                target_provider = "insanely-fast"
                logger.info(f"[PrometheusRouter V6.6] Model '{requested_model}' -> Default: GPU Accuracy")

            # 활성 카운트 증가
            await increment_active_count(target_provider)

            try:
                # Redis Stream을 통한 Transcription 요청
                language = data.get("language", "ko")

                logger.info(f"[PrometheusRouter V6.6] Sending transcription via Redis Stream: model={target_model}")
                result = await gpu_client.request_transcription(
                    audio_file_path=temp_file,
                    model=target_model,
                    language=language,
                    timeout=1800.0,
                )

                logger.info(f"[PrometheusRouter V6.6] Transcription completed")

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

            except Exception as e:
                # Fallback 로직 (Speed 모드에서 NPU 실패 시 -> GPU Whisper.cpp)
                if is_speed_mode:
                    logger.warning(f"[PrometheusRouter V6.6] NPU failed: {e}. Trying Fallback to whisper-cpp...")
                    await decrement_active_count("flm")

                    target_provider = "whisper-cpp"
                    await increment_active_count(target_provider)

                    try:
                        result = await gpu_client.request_transcription(
                            audio_file_path=temp_file,
                            model="whisper-turbo",
                            language=data.get("language", "ko"),
                            timeout=1800.0,
                        )

                        return ModelResponse(
                            id=result.get("id", f"transcribe-{uuid.uuid4()}"),
                            created=int(time.time()),
                            model="whisper-turbo",
                            object="text",
                            choices=[
                                {
                                    "text": result.get("text", ""),
                                    "segments": result.get("segments", []),
                                    "language": result.get("language", ""),
                                }
                            ],
                        )
                    except Exception as fallback_error:
                        logger.error(f"[PrometheusRouter V6.6] Fallback failed: {fallback_error}")
                        raise fallback_error
                else:
                    logger.error(f"[PrometheusRouter V6.6] Transcription failed: {e}")
                    raise e
            finally:
                await decrement_active_count(target_provider)

        finally:
            # 임시 파일 정리
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

# LiteLLM에 등록할 핸들러 인스턴스
prometheus_router = PrometheusRouter()

