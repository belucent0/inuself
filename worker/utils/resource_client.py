"""LiteLLM Resource Management Client.

Worker에서 LiteLLM의 /resource/acquire, /resource/release 엔드포인트를 호출합니다.
중앙집중 방식으로 리소스 경합을 방지합니다.
"""
import os
import httpx
from contextlib import contextmanager
from typing import Optional, Generator
from worker.logging_config import logger

# LiteLLM Base URL
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
RESOURCE_ACQUIRE_URL = f"{LITELLM_BASE_URL}/resource/acquire"
RESOURCE_RELEASE_URL = f"{LITELLM_BASE_URL}/resource/release"

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
    LiteLLM에서 리소스 획득.

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
    LiteLLM에서 리소스 해제.

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
GPU_DEVICE_ID = os.getenv("GPU_DEVICE_ID", "0x000142B6")
NPU_DEVICE_ID = os.getenv("NPU_DEVICE_ID", "0x000160E6")
BUSY_THRESHOLD = 70  # 70% 이상이면 "바쁨"


def _query_prometheus_sync(device_id: str) -> float:
    """Prometheus에서 1분 평균 사용량 조회 (동기)."""
    query = f'''
    avg_over_time(
      sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{device_id}.*engtype_Compute.*"}})
    [1m])
    '''
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

    # Prometheus 조회
    try:
        npu_usage = _query_prometheus_sync(NPU_DEVICE_ID)
        gpu_usage = _query_prometheus_sync(GPU_DEVICE_ID)

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
