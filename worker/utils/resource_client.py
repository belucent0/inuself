"""AI Gateway Resource Management Client.

Worker에서 AI Gateway의 /resource/acquire, /resource/release 엔드포인트를 호출합니다.
중앙집중 방식으로 리소스 경합을 방지합니다.

v1.2.0 현행: ai-gateway가 추론 컨테이너(ai-llm/ai-asr/ai-ocr/ai-diarize/ai-embedding)를
httpx로 직접 호출하며, provider 선택은 ai-gateway 내부에서 추상화됩니다.
본 클라이언트는 워커 측 동시 실행 제어(GPU/NPU 자원 잠금)에 사용됩니다.
"""
import os
import httpx
from contextlib import contextmanager
from typing import Optional, Generator
from worker.logging_config import logger

# AI Gateway Base URL
AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://ai-gateway:4000")
RESOURCE_ACQUIRE_URL = f"{AI_GATEWAY_URL}/resource/acquire"
RESOURCE_RELEASE_URL = f"{AI_GATEWAY_URL}/resource/release"

# 기본 타임아웃 (초)
DEFAULT_ACQUIRE_TIMEOUT = 120.0  # 리소스 획득 대기 시간
HTTP_TIMEOUT = 180.0  # HTTP 요청 타임아웃


class ResourceAcquisitionError(Exception):
    """리소스 획득 실패 예외."""
    pass


class ResourceInfo:
    """획득한 리소스 정보."""
    def __init__(self, resource_type: str, task_type: str, task_id: str,
                 provider: str, api_base: str, wait_time: float):
        self.resource_type = resource_type
        self.task_type = task_type
        self.task_id = task_id
        self.provider = provider
        self.api_base = api_base
        self.wait_time = wait_time

    def __repr__(self):
        return (f"ResourceInfo(type={self.resource_type}/{self.task_type}, "
                f"provider={self.provider}, wait={self.wait_time:.2f}s)")


def acquire_resource(
    resource_type: str,
    task_type: str,
    task_id: str,
    accuracy_mode: str = "speed",
    timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
) -> ResourceInfo:
    """
    AI Gateway에서 리소스 획득.

    Args:
        resource_type: "gpu" 또는 "npu"
        task_type: "asr", "ocr", "llm", "diarization"
        task_id: 고유 작업 ID (Celery task ID 등)
        accuracy_mode: ASR 정확도 모드 ("speed" 또는 "accuracy")
        timeout: 최대 대기 시간 (초)

    Returns:
        ResourceInfo: 획득한 리소스 정보

    Raises:
        ResourceAcquisitionError: 리소스 획득 실패 시
    """
    logger.info(
        "[Resource] Acquiring: type={}/{}, task={}, accuracy={}, timeout={}s",
        resource_type, task_type, task_id, accuracy_mode, timeout
    )

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.post(
                RESOURCE_ACQUIRE_URL,
                json={
                    "resource_type": resource_type,
                    "task_type": task_type,
                    "task_id": task_id,
                    "accuracy_mode": accuracy_mode,
                    "timeout": timeout,
                }
            )
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                info = ResourceInfo(
                    resource_type=resource_type,
                    task_type=task_type,
                    task_id=task_id,
                    provider=data.get("provider", ""),
                    api_base=data.get("api_base", ""),
                    wait_time=data.get("wait_time", 0.0),
                )
                logger.info("[Resource] Acquired: {}", info)
                return info
            else:
                error_msg = data.get("message", "Unknown error")
                logger.warning("[Resource] Acquisition failed: {}", error_msg)
                raise ResourceAcquisitionError(error_msg)

    except httpx.HTTPStatusError as e:
        logger.error("[Resource] HTTP error: {} - {}", e.response.status_code, e.response.text)
        raise ResourceAcquisitionError(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error("[Resource] Request error: {}", e)
        raise ResourceAcquisitionError(f"Request failed: {e}")


def release_resource(resource_type: str, task_type: str, task_id: str) -> bool:
    """
    AI Gateway에서 리소스 해제.

    Args:
        resource_type: "gpu" 또는 "npu"
        task_type: "asr", "ocr", "llm", "diarization"
        task_id: 고유 작업 ID

    Returns:
        bool: 성공 여부
    """
    logger.info("[Resource] Releasing: type={}/{}, task={}", resource_type, task_type, task_id)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                RESOURCE_RELEASE_URL,
                json={
                    "resource_type": resource_type,
                    "task_type": task_type,
                    "task_id": task_id,
                }
            )
            response.raise_for_status()
            data = response.json()

            success = data.get("success", False)
            message = data.get("message", "")
            if success:
                logger.info("[Resource] Released: {}/{} - {}", resource_type, task_type, message)
            else:
                logger.warning("[Resource] Release failed: {}", message)
            return success

    except Exception as e:
        logger.error("[Resource] Release error: {}", e)
        return False


@contextmanager
def resource_lock(
    resource_type: str,
    task_type: str,
    task_id: str,
    accuracy_mode: str = "speed",
    timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
) -> Generator[ResourceInfo, None, None]:
    """
    리소스 락 컨텍스트 매니저.

    with 블록 진입 시 리소스를 획득하고, 종료 시 자동으로 해제합니다.

    Example:
        with resource_lock("gpu", "asr", task_id, accuracy_mode="speed") as resource:
            # resource.provider, resource.api_base 사용
            process_asr(resource.api_base)

    Args:
        resource_type: "gpu" 또는 "npu"
        task_type: "asr", "ocr", "llm", "diarization"
        task_id: 고유 작업 ID
        accuracy_mode: ASR 정확도 모드
        timeout: 최대 대기 시간

    Yields:
        ResourceInfo: 획득한 리소스 정보

    Raises:
        ResourceAcquisitionError: 리소스 획득 실패 시
    """
    resource_info = None
    try:
        resource_info = acquire_resource(
            resource_type=resource_type,
            task_type=task_type,
            task_id=task_id,
            accuracy_mode=accuracy_mode,
            timeout=timeout,
        )
        yield resource_info
    finally:
        if resource_info:
            release_resource(resource_type, task_type, task_id)


def select_resource_type(task_type: str, accuracy_mode: str = "speed") -> str:
    """
    작업 타입과 정확도 모드에 따라 리소스 타입 선택.

    현재 구현에서는 단순하게:
    - speed 모드: NPU 우선 (없으면 GPU)
    - accuracy 모드: GPU

    Args:
        task_type: "asr", "ocr", "llm", "diarization"
        accuracy_mode: "speed" 또는 "accuracy"

    Returns:
        "gpu" 또는 "npu"
    """
    if accuracy_mode == "accuracy":
        return "gpu"

    # speed 모드
    if task_type == "diarization":
        return "gpu"  # diarization은 GPU만 지원
    elif task_type in ("asr", "llm", "ocr"):
        return "npu"  # speed 모드는 NPU 우선
    else:
        return "gpu"  # 기본값


# ============================================================
# Prometheus 기반 동적 리소스 선택
# ============================================================
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
BUSY_THRESHOLD = 70  # 70% 이상이면 "바쁨"

# 캐시된 디바이스 ID (동적 감지 - LUID는 리부팅 시 변경될 수 있음)
_cached_gpu_luid: str | None = None
_cached_npu_luid: str | None = None


def _detect_device_luids() -> tuple[str, str]:
    """Prometheus에서 디바이스 LUID를 동적으로 감지.

    디바이스 특성 기반 감지:
    - NVIDIA GPU: 3D 엔진만 있고 Video 엔진이 없음
    - AMD NPU: Compute 엔진만 있고 3D/Video 엔진이 없음
    - AMD iGPU: 3D + Video + Compute 혼합

    Returns:
        (gpu_luid, npu_luid) 튜플
    """
    import re
    from collections import defaultdict

    gpu_luid = ""
    npu_luid = ""

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
                luid_engines: dict[str, set[str]] = defaultdict(set)

                for result in data["data"]["result"]:
                    metric = result["metric"]
                    exported_instance = metric.get("exported_instance", "")

                    luid_match = re.search(r'luid_0x[0-9A-Fa-f]+_0x([0-9A-Fa-f]+)', exported_instance)
                    if not luid_match:
                        continue
                    luid = '0x' + luid_match.group(1).lower()

                    engtype_match = re.search(r'engtype_([A-Za-z0-9_ ]+)', exported_instance)
                    if engtype_match:
                        luid_engines[luid].add(engtype_match.group(1).strip())

                for luid, engines in luid_engines.items():
                    has_video = any('Video' in e for e in engines)
                    has_compute = any('Compute' in e for e in engines)
                    has_3d = any('3D' in e for e in engines)

                    # AMD iGPU: Video 엔진이 있음 (실제 GPU)
                    if has_video and not gpu_luid:
                        gpu_luid = luid
                        logger.info("[Resource] Detected AMD iGPU: {}", gpu_luid)

                    # AMD NPU: Compute만 있고 3D/Video 없음
                    if has_compute and not has_3d and not has_video and not npu_luid:
                        npu_luid = luid
                        logger.info("[Resource] Detected AMD NPU: {}", npu_luid)

    except Exception as e:
        logger.warning("[Resource] Device detection failed: {}", e)

    return gpu_luid, npu_luid


def _query_prometheus_sync(device_type: str) -> float:
    """Prometheus에서 1분 평균 사용량 조회 (동기).

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        0-100 사이의 사용률
    """
    global _cached_gpu_luid, _cached_npu_luid

    # 디바이스 LUID 캐싱
    if _cached_gpu_luid is None or _cached_npu_luid is None:
        _cached_gpu_luid, _cached_npu_luid = _detect_device_luids()

    if device_type == "gpu":
        if not _cached_gpu_luid:
            return 0.0
        # GPU: 3D 엔진 사용률
        query = f'avg_over_time(sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_gpu_luid}.*engtype_3D.*"}})[1m])'
    else:
        if not _cached_npu_luid:
            return 0.0
        # NPU: Compute 엔진 사용률
        query = f'avg_over_time(sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{_cached_npu_luid}.*engtype_Compute.*"}})[1m])'

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                return float(data["data"]["result"][0]["value"][1])
            return 0.0
    except Exception as e:
        logger.warning("[Resource] Prometheus query failed: {}", e)
        return 0.0


def select_resource_type_dynamic(task_type: str, accuracy_mode: str = "speed") -> str:
    """
    Prometheus 기반 동적 리소스 타입 선택.

    V6.5: 단일 FLM 서버로 OCR/LLM 통합, 메모리 문제 해결

    NPU 우선 방식:
    1. NPU 사용량 < 70%: NPU 사용
    2. NPU 바쁘고 GPU 사용량 < 70%: GPU 사용
    3. 둘 다 바쁨: NPU 사용 (덜 바쁜 쪽)

    Args:
        task_type: "asr", "ocr", "llm", "diarization"
        accuracy_mode: "speed" 또는 "accuracy"

    Returns:
        "gpu" 또는 "npu"
    """
    # accuracy 모드는 GPU만 사용
    if accuracy_mode == "accuracy":
        logger.info("[Resource] Accuracy mode -> GPU (forced)")
        return "gpu"

    # diarization은 GPU만 지원
    if task_type == "diarization":
        logger.info("[Resource] Diarization -> GPU (forced)")
        return "gpu"

    # V6.5: OCR 신속모드는 NPU 사용 가능 (통합 FLM 서버로 메모리 문제 해결)
    # (이전 버전에서는 OCR을 GPU로 강제했지만, V6.5에서는 불필요)

    # Prometheus 조회
    try:
        npu_usage = _query_prometheus_sync("npu")
        gpu_usage = _query_prometheus_sync("gpu")

        logger.info(
            "[Resource] Usage (1m avg): NPU={:.1f}%, GPU={:.1f}%",
            npu_usage, gpu_usage
        )

        # NPU 우선 선택
        if npu_usage < BUSY_THRESHOLD:
            logger.info("[Resource] Selected NPU (usage {:.1f}% < {}%)", npu_usage, BUSY_THRESHOLD)
            return "npu"
        elif gpu_usage < BUSY_THRESHOLD:
            logger.info("[Resource] Selected GPU (NPU busy, usage {:.1f}% < {}%)", gpu_usage, BUSY_THRESHOLD)
            return "gpu"
        else:
            # 둘 다 바쁨 → 덜 바쁜 쪽 선택
            if npu_usage <= gpu_usage:
                logger.info("[Resource] Both busy, selected NPU (less busy: {:.1f}% vs {:.1f}%)", npu_usage, gpu_usage)
                return "npu"
            else:
                logger.info("[Resource] Both busy, selected GPU (less busy: {:.1f}% vs {:.1f}%)", gpu_usage, npu_usage)
                return "gpu"
    except Exception as e:
        # Prometheus 실패 시 GPU fallback (안정성)
        logger.warning("[Resource] Prometheus failed, fallback to GPU: {}", e)
        return "gpu"
