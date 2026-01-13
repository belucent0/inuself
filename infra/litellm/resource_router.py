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
GPU_DEVICE_ID = os.getenv("GPU_DEVICE_ID", "0x000142B6")
NPU_DEVICE_ID = os.getenv("NPU_DEVICE_ID", "0x000160E6")

# 임계값
BUSY_THRESHOLD = 70  # 70% 이상이면 "바쁨"
LOW_THRESHOLD = 30   # 30% 이하면 "곧 끝나가는 중"
MAX_RETRY = 3        # 재시도 최대 횟수

# ============================================
# Phase 2: Redis 세마포어 (워커 세마포어 구현 후 활성화)
# ============================================
# import redis.asyncio as redis
# redis_client = None
# 
# async def get_redis():
#     global redis_client
#     if redis_client is None:
#         redis_client = redis.from_url(REDIS_URL)
#     return redis_client


async def query_prometheus(device_id: str) -> float:
    """Prometheus에서 1분 평균 사용량 조회."""
    query = f'''
    avg_over_time(
      sum(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{device_id}.*engtype_Compute.*"}})
    [1m])
    '''
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "success" and data["data"]["result"]:
                return float(data["data"]["result"][0]["value"][1])
            return 0.0
    except Exception as e:
        logger.warning(f"Prometheus query failed: {e}")
        return 0.0


async def select_provider(retry_count: int = 0) -> str:
    """사용 가능한 Provider를 선택합니다.
    
    Phase 1: Prometheus 평균 사용량만 사용
    Phase 2: 세마포어 추가 (주석 해제 필요)
    """
    
    # ============================================
    # Phase 2: 세마포어 (워커 세마포어 구현 후 활성화)
    # ============================================
    # r = await get_redis()
    # gpu_active = await r.exists("worker:gpu:active")
    # npu_active = await r.exists("worker:npu:active")
    # 
    # logger.info(f"Worker status - GPU: {'active' if gpu_active else 'idle'}, NPU: {'active' if npu_active else 'idle'}")
    # 
    # # 비활성 워커가 있으면 즉시 사용 (NPU 우선)
    # if not npu_active:
    #     return "npu"
    # if not gpu_active:
    #     return "gpu"
    
    # ============================================
    # Phase 1: Prometheus 평균 사용량만 (현재 활성)
    # ============================================
    gpu_avg = await query_prometheus(GPU_DEVICE_ID)
    npu_avg = await query_prometheus(NPU_DEVICE_ID)
    
    logger.info(f"Usage avg (1m) - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}%")
    
    # NPU 우선 (기존 동작 유지)
    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"Selected: NPU (usage {npu_avg:.1f}% < {BUSY_THRESHOLD}%)")
        return "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"Selected: GPU (usage {gpu_avg:.1f}% < {BUSY_THRESHOLD}%)")
        return "gpu"
    
    # 사용량이 낮으면 잠시 대기 후 재시도 (곧 끝날 가능성)
    if (npu_avg < LOW_THRESHOLD or gpu_avg < LOW_THRESHOLD) and retry_count < MAX_RETRY:
        logger.info(f"Low usage detected, waiting 500ms (retry {retry_count + 1}/{MAX_RETRY})...")
        await asyncio.sleep(0.5)
        return await select_provider(retry_count + 1)
    
    # 둘 다 바쁨 → 큐
    logger.warning("All providers busy, will queue request")
    raise ResourceBusyException("All providers busy")


class ResourceBusyException(Exception):
    """모든 Provider가 바쁠 때 발생하는 예외."""
    pass
