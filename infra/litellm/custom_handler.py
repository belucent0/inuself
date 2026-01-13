"""LiteLLM Custom Handler - Prometheus 기반 GPU/NPU 라우팅.

사용량이 낮은 Provider를 선택하여 요청을 전달합니다. (진짜 스트리밍 지원)
"""
import os
import json
import logging
import time
import uuid
from typing import Iterator, AsyncIterator, Optional, Any

import httpx
import redis
import redis.asyncio as redis_async

import litellm
from litellm import CustomLLM
from litellm.types.utils import GenericStreamingChunk, ModelResponse

logger = logging.getLogger(__name__)

# 환경변수
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://asr-prometheus:9090")
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-redis:6379/0")


# 기본값은 호스트 Docker 내부 주소 (LiteLLM 컨테이너 -> 호스트)
GPU_API_BASE = os.getenv("GPU_API_BASE", "http://host.docker.internal:8080")
NPU_API_BASE = os.getenv("NPU_API_BASE", "http://host.docker.internal:11434")

# 장치 ID
GPU_DEVICE_ID = os.getenv("GPU_DEVICE_ID", "0x000142B6")
NPU_DEVICE_ID = os.getenv("NPU_DEVICE_ID", "0x000160E6")

# 모델명
GPU_MODEL = os.getenv("GPU_MODEL", "qwen3-4b")
NPU_MODEL = os.getenv("NPU_MODEL", "qwen3-it:4b")

# 임계값
BUSY_THRESHOLD = 70  # 70% 이상이면 "바쁨"

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


async def select_provider_async() -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (비동기)."""
    # 1. Redis 세마포어 체크 (즉시 반응)
    if redis_client_async:
        try:
            npu_active = await redis_client_async.exists("worker:npu:active")
            if npu_active:
                logger.info("[CustomRouter] Selected: GPU (Reason: NPU Semaphore Active)")
                return GPU_API_BASE, GPU_MODEL, "gpu"
        except Exception as e:
            logger.warning(f"Redis semaphore check failed: {e}")

    # 2. Prometheus 메트릭 체크
    gpu_avg = await query_prometheus_async(GPU_DEVICE_ID)
    npu_avg = await query_prometheus_async(NPU_DEVICE_ID)
    
    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}%")
    
    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: NPU (usage {npu_avg:.1f}%)")
        return NPU_API_BASE, NPU_MODEL, "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: GPU (usage {gpu_avg:.1f}%)")
        return GPU_API_BASE, GPU_MODEL, "gpu"
    
    logger.warning(f"[CustomRouter] All busy, defaulting to NPU")
    return NPU_API_BASE, NPU_MODEL, "npu"


def select_provider_sync() -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (동기)."""
    # 1. Redis 세마포어 체크 (즉시 반응)
    if redis_client_sync:
        try:
            npu_active = redis_client_sync.exists("worker:npu:active")
            if npu_active:
                logger.info("[CustomRouter] Selected: GPU (Reason: NPU Semaphore Active)")
                return GPU_API_BASE, GPU_MODEL, "gpu"
        except Exception as e:
            logger.warning(f"Redis semaphore check failed: {e}")

    # 2. Prometheus 메트릭 체크
    gpu_avg = query_prometheus_sync(GPU_DEVICE_ID)
    npu_avg = query_prometheus_sync(NPU_DEVICE_ID)
    
    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}%")
    
    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: NPU (usage {npu_avg:.1f}%)")
        return NPU_API_BASE, NPU_MODEL, "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: GPU (usage {gpu_avg:.1f}%)")
        return GPU_API_BASE, GPU_MODEL, "gpu"
    
    logger.warning(f"[CustomRouter] All busy, defaulting to NPU")
    return NPU_API_BASE, NPU_MODEL, "npu"



class PrometheusRouter(CustomLLM):
    """Prometheus 메트릭 기반 GPU/NPU 라우터."""
    
    def completion(self, *args, **kwargs) -> ModelResponse:
        """동기 completion (Non-streaming)."""
        messages = kwargs.get("messages", [])
        api_base, model, provider = select_provider_sync()
        
        logger.info(f"[PrometheusRouter] Routing to {provider}")
        
        url = f"{api_base}/v1/chat/completions"
        payload = {"model": model, "messages": messages, "stream": False}
        
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

    async def acompletion(self, *args, **kwargs) -> ModelResponse:
        """비동기 completion (Non-streaming)."""
        # 간단하게 동기 메서드 호출 (비동기 최적화 생략)
        return self.completion(*args, **kwargs)
    
    async def astreaming(self, *args, **kwargs) -> AsyncIterator[GenericStreamingChunk]:
        """비동기 스트리밍 (Real Streaming)."""
        messages = kwargs.get("messages", [])
        api_base, model, provider = await select_provider_async()
        
        logger.info(f"[PrometheusRouter] Streaming to {provider}")
        
        url = f"{api_base}/v1/chat/completions"
        payload = {"model": model, "messages": messages, "stream": True}
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
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
                                usage=None,  # 스트리밍 중에는 사용량 정보가 보통 없음
                            )
                        except Exception as e:
                            logger.error(f"Streaming parse error: {e}")

# LiteLLM에 등록할 핸들러 인스턴스
prometheus_router = PrometheusRouter()
