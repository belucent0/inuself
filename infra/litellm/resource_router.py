"""세마포어 + Prometheus 메트릭 기반 LLM Provider 라우터.

Phase 1: Prometheus 평균 사용량만 사용 (현재 활성)
Phase 2: 세마포어 추가로 정확도 향상 (후순위, 주석 해제)
"""
import os
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

# 환경변수
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
GPU_ENGTYPE = "Compute"

# 임계값
BUSY_THRESHOLD = 70
LOW_THRESHOLD = 30
MAX_RETRY = 3


def get_gpu_device_ids_sync() -> list[str]:
    """Prometheus에서 GPU device ID 목록을 동적으로 조회."""
    import re
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
                device_ids = []
                for result in data["data"]["result"]:
                    metric = result["metric"]
                    exported_instance = metric.get("exported_instance", "")
                    match = re.search(r'luid_0x[0-9A-Fa-f]+_0x([0-9A-Fa-f]+)', exported_instance)
                    if match:
                        luid = '0x' + match.group(1).lower()
                        if luid not in device_ids:
                            device_ids.append(luid)
                return device_ids
            return []
    except Exception as e:
        logger.warning(f"[Prometheus] Failed to get GPU device IDs: {e}")
        return []


def get_npu_device_ids_sync() -> list[str]:
    """Prometheus에서 NPU device ID 목록을 동적으로 조회."""
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
                return device_ids
    except Exception:
        pass

    return []


async def query_single_device(device_id: str, engine_filter: str = "non_compute") -> float:
    """Query Prometheus for GPU/NPU utilization.

    Prometheus Metrics Reference (Grafana Dashboard aligned):
    - GPU:  windows_gpu_engine_utilization_percentage{engtype=~"3D|Video"}
           sum() 사용 (Windows 작업 관리자 GPU 사용률과 일치)
    - NPU:  windows_gpu_engine_utilization_percentage{engtype="Compute"}
           max() 사용 (여러 엔진 중 가장 높은 값)

    Args:
        device_id: Device ID (e.g., "0x0001392E")
        engine_filter: "non_compute" for GPU (3D/Video), "compute" for NPU

    Returns:
        Utilization percentage (0-100)
    """
    if engine_filter == "non_compute":
        # GPU: sum(3D + Video) - 0-100% 범위
        query = 'sum(windows_gpu_engine_utilization_percentage{exported_instance=~".*engtype_(3D|Video).*"})'
    else:
        # NPU: max(Compute) - 0-100% 범위
        query = 'max(windows_gpu_engine_utilization_percentage{exported_instance=~".*engtype_Compute.*"})'

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


async def query_prometheus(device_type: str = "gpu") -> float:
    """Prometheus에서 사용량을 동적으로 조회.

    Prometheus Metrics Reference:
    - GPU:  sum(windows_gpu_engine_utilization_percentage{engtype=~"3D|Video"})
    - NPU:  max(windows_gpu_engine_utilization_percentage{engtype="Compute"})

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        0-100 사이의 사용량
    """
    device_ids = get_gpu_device_ids_sync()
    if not device_ids:
        logger.warning("[Prometheus] No GPU/NPU devices found, returning 0")
        return 0.0

    if device_type == "gpu":
        query = 'sum(windows_gpu_engine_utilization_percentage{exported_instance=~".*engtype_(3D|Video).*"})'
    elif device_type == "npu":
        query = 'max(windows_gpu_engine_utilization_percentage{exported_instance=~".*engtype_Compute.*"})'
    else:
        return 0.0

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
                logger.debug(f"[Prometheus] {device_type.upper()}: {value:.1f}%")
                return value
            return 0.0
    except Exception as e:
        logger.warning(f"Prometheus query failed for {device_type}: {e}")
        return 0.0


async def query_memory(device_type: str = "gpu") -> dict:
    """Prometheus에서 GPU/NPU 메모리 사용량을 조회.

    Prometheus Metrics Reference:
    - GPU: windows_gpu_dedicated_memory_usage_bytes
           windows_gpu_shared_memory_usage_bytes
    - NPU: windows_gpu_shared_memory_usage_bytes{engtype=Compute}
           windows_gpu_total_committed_bytes{engtype=Compute}

    Args:
        device_type: "gpu" 또는 "npu"

    Returns:
        {"dedicated": bytes, "shared": bytes, "total": bytes}
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
        return {"dedicated": 0, "shared": 0, "total": 0}

    result = {"dedicated": 0, "shared": 0, "total": 0}

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

    except Exception as e:
        logger.warning(f"Prometheus memory query failed for {device_type}: {e}")

    return result


def format_bytes(b: float) -> str:
    """바이트를 읽기 쉬운 단위로 변환."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.1f}{unit}"
        b /= 1024.0
    return f"{b:.1f}PB"


async def select_provider(retry_count: int = 0) -> str:
    """사용 가능한 Provider를 선택합니다."""
    gpu_avg = await query_prometheus("gpu")
    npu_avg = await query_prometheus("npu")
    gpu_mem = await query_memory("gpu")
    npu_mem = await query_memory("npu")

    logger.info(f"Usage (1m) - GPU: {gpu_avg:.1f}% ({format_bytes(gpu_mem['total'])}), NPU: {npu_avg:.1f}% ({format_bytes(npu_mem['total'])})")

    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"Selected: NPU (usage {npu_avg:.1f}% < {BUSY_THRESHOLD}%)")
        return "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"Selected: GPU (usage {gpu_avg:.1f}% < {BUSY_THRESHOLD}%)")
        return "gpu"

    if (npu_avg < LOW_THRESHOLD or gpu_avg < LOW_THRESHOLD) and retry_count < MAX_RETRY:
        logger.info(f"Low usage detected, waiting 500ms (retry {retry_count + 1}/{MAX_RETRY})...")
        await asyncio.sleep(0.5)
        return await select_provider(retry_count + 1)

    logger.warning("All providers busy, will queue request")
    raise ResourceBusyException("All providers busy")


class ResourceBusyException(Exception):
    """모든 Provider가 바쁠 때 발생하는 예외."""
    pass
