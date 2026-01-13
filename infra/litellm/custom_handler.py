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

# Monkeypatch provider list to allow custom provider name
if not hasattr(litellm, "provider_list"):
    litellm.provider_list = []
if "prometheus-router" not in litellm.provider_list:
    litellm.provider_list.append("prometheus-router")

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

# Chat 모델명
GPU_MODEL = os.getenv("GPU_MODEL", "qwen3-4b")
NPU_MODEL = os.getenv("NPU_MODEL", "qwen3-it:4b")

# Audio 설정
GPU_AUDIO_API_BASE = os.getenv("GPU_AUDIO_API_BASE", "http://host.docker.internal:8001")
NPU_AUDIO_API_BASE = os.getenv("NPU_AUDIO_API_BASE", "http://host.docker.internal:11434")
GPU_AUDIO_MODEL = os.getenv("GPU_AUDIO_MODEL", "whisper-turbo")
NPU_AUDIO_MODEL = os.getenv("NPU_AUDIO_MODEL", "flm-audio")

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


async def send_provider_start_signal(provider: str):
    """Provider Manager에게 시작 신호 전송 (비동기).
    
    Args:
        provider: 'flm' (NPU) or 'llama' (GPU)
    """
    if not redis_client_async:
        logger.warning("[CustomRouter] Redis not available, skipping provider start signal")
        return
    
    try:
        message = {"action": "start", "provider": provider}
        await redis_client_async.publish("provider.control", json.dumps(message))
        logger.info(f"[CustomRouter] Sent provider start signal: {provider}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to send provider start signal: {e}")


async def select_provider_async(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (비동기).

    Args:
        task_type: "chat" or "audio"
        force_provider: 강제 선택 - "gpu" or "npu" (None이면 자동 선택)

    Returns:
        (api_base, model, provider_name)
    """
    # 0. 강제 선택이 있으면 바로 반환
    if force_provider == "gpu":
        logger.info(f"[CustomRouter] Forced: GPU (task_type={task_type})")
        await send_provider_start_signal("llama")
        if task_type == "audio":
            return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
        return GPU_API_BASE, GPU_MODEL, "gpu"
    elif force_provider == "npu":
        logger.info(f"[CustomRouter] Forced: NPU (task_type={task_type})")
        await send_provider_start_signal("flm")
        if task_type == "audio":
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_API_BASE, NPU_MODEL, "npu"

    # 1. Redis 세마포어 체크 (즉시 반응)
    if redis_client_async:
        try:
            # Audio의 경우 별도 세마포어 사용 가능 (현재는 공통 사용 가정)
            npu_active = await redis_client_async.exists("worker:npu:active")
            if npu_active:
                logger.info(f"[CustomRouter] Selected: GPU (Reason: NPU Semaphore Active)")
                await send_provider_start_signal("llama")  # GPU 선택 → llama 시작
                if task_type == "audio":
                    return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
                return GPU_API_BASE, GPU_MODEL, "gpu"
        except Exception as e:
            logger.warning(f"Redis semaphore check failed: {e}")

    # 2. Prometheus 메트릭 체크
    gpu_avg = await query_prometheus_async(GPU_DEVICE_ID)
    npu_avg = await query_prometheus_async(NPU_DEVICE_ID)

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}%")

    # Audio Task Routing
    if task_type == "audio":
        if npu_avg < BUSY_THRESHOLD:
            logger.info(f"[CustomRouter] Selected: NPU Audio (usage {npu_avg:.1f}%)")
            await send_provider_start_signal("flm")  # NPU 선택 → flm 시작
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        elif gpu_avg < BUSY_THRESHOLD:
            logger.info(f"[CustomRouter] Selected: GPU Audio (usage {gpu_avg:.1f}%)")
            await send_provider_start_signal("llama")  # GPU 선택 → llama 시작
            return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"

        logger.warning(f"[CustomRouter] All busy, defaulting to NPU Audio")
        await send_provider_start_signal("flm")  # 기본값 NPU → flm 시작
        return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"

    # Chat Task Routing
    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: NPU (usage {npu_avg:.1f}%)")
        await send_provider_start_signal("flm")  # NPU 선택 → flm 시작
        return NPU_API_BASE, NPU_MODEL, "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: GPU (usage {gpu_avg:.1f}%)")
        await send_provider_start_signal("llama")  # GPU 선택 → llama 시작
        return GPU_API_BASE, GPU_MODEL, "gpu"

    logger.warning(f"[CustomRouter] All busy, defaulting to NPU")
    await send_provider_start_signal("flm")  # 기본값 NPU → flm 시작
    return NPU_API_BASE, NPU_MODEL, "npu"


def send_provider_start_signal_sync(provider: str):
    """Provider Manager에게 시작 신호 전송 (동기).
    
    Args:
        provider: 'flm' (NPU) or 'llama' (GPU)
    """
    if not redis_client_sync:
        logger.warning("[CustomRouter] Redis not available, skipping provider start signal")
        return
    
    try:
        message = {"action": "start", "provider": provider}
        redis_client_sync.publish("provider.control", json.dumps(message))
        logger.info(f"[CustomRouter] Sent provider start signal (sync): {provider}")
    except Exception as e:
        logger.warning(f"[CustomRouter] Failed to send provider start signal: {e}")


def select_provider_sync(
    task_type: str = "chat",
    force_provider: Optional[str] = None,
) -> tuple[str, str, str]:
    """사용 가능한 Provider를 선택합니다 (동기).

    Args:
        task_type: "chat" or "audio"
        force_provider: 강제 선택 - "gpu" or "npu" (None이면 자동 선택)

    Returns:
        (api_base, model, provider_name)
    """
    # 0. 강제 선택이 있으면 바로 반환
    if force_provider == "gpu":
        logger.info(f"[CustomRouter] Forced: GPU (task_type={task_type})")
        send_provider_start_signal_sync("llama")
        if task_type == "audio":
            return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
        return GPU_API_BASE, GPU_MODEL, "gpu"
    elif force_provider == "npu":
        logger.info(f"[CustomRouter] Forced: NPU (task_type={task_type})")
        send_provider_start_signal_sync("flm")
        if task_type == "audio":
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        return NPU_API_BASE, NPU_MODEL, "npu"

    # 1. Redis 세마포어 체크 (즉시 반응)
    if redis_client_sync:
        try:
            npu_active = redis_client_sync.exists("worker:npu:active")
            if npu_active:
                logger.info(f"[CustomRouter] Selected: GPU (Reason: NPU Semaphore Active)")
                send_provider_start_signal_sync("llama")  # GPU 선택 → llama 시작
                if task_type == "audio":
                    return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"
                return GPU_API_BASE, GPU_MODEL, "gpu"
        except Exception as e:
            logger.warning(f"Redis semaphore check failed: {e}")

    # 2. Prometheus 메트릭 체크
    gpu_avg = query_prometheus_sync(GPU_DEVICE_ID)
    npu_avg = query_prometheus_sync(NPU_DEVICE_ID)

    logger.info(f"[CustomRouter] Usage - GPU: {gpu_avg:.1f}%, NPU: {npu_avg:.1f}%")

    # Audio Task Routing
    if task_type == "audio":
        if npu_avg < BUSY_THRESHOLD:
            logger.info(f"[CustomRouter] Selected: NPU Audio (usage {npu_avg:.1f}%)")
            send_provider_start_signal_sync("flm")  # NPU 선택 → flm 시작
            return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"
        elif gpu_avg < BUSY_THRESHOLD:
            logger.info(f"[CustomRouter] Selected: GPU Audio (usage {gpu_avg:.1f}%)")
            send_provider_start_signal_sync("llama")  # GPU 선택 → llama 시작
            return GPU_AUDIO_API_BASE, GPU_AUDIO_MODEL, "gpu-audio"

        logger.warning(f"[CustomRouter] All busy, defaulting to NPU Audio")
        send_provider_start_signal_sync("flm")  # 기본값 NPU → flm 시작
        return NPU_AUDIO_API_BASE, NPU_AUDIO_MODEL, "npu-audio"

    # Chat Task Routing
    if npu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: NPU (usage {npu_avg:.1f}%)")
        send_provider_start_signal_sync("flm")  # NPU 선택 → flm 시작
        return NPU_API_BASE, NPU_MODEL, "npu"
    elif gpu_avg < BUSY_THRESHOLD:
        logger.info(f"[CustomRouter] Selected: GPU (usage {gpu_avg:.1f}%)")
        send_provider_start_signal_sync("llama")  # GPU 선택 → llama 시작
        return GPU_API_BASE, GPU_MODEL, "gpu"

    logger.warning(f"[CustomRouter] All busy, defaulting to NPU")
    send_provider_start_signal_sync("flm")  # 기본값 NPU → flm 시작
    return NPU_API_BASE, NPU_MODEL, "npu"



class PrometheusRouter(CustomLLM):
    """Prometheus 메트릭 기반 GPU/NPU 라우터."""
    streaming = True  # Signal to LiteLLM that this provider supports streaming
    
    def completion(self, *args, **kwargs) -> ModelResponse:
        """동기 completion (Non-streaming)."""
        messages = kwargs.get("messages", [])
        
        # Debugging: check if stream is hidden in nested params
        litellm_params = kwargs.get("litellm_params", {})
        optional_params = kwargs.get("optional_params", {})
        stream = kwargs.get("stream", False) or litellm_params.get("stream") or optional_params.get("stream")
        
        logger.info(f"[PrometheusRouter] completion called. stream={stream}")
        if stream:
             logger.warning("[PrometheusRouter] stream=True detected in completion. LiteLLM should have called astreaming.")

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
        stream = kwargs.get("stream", False)
        litellm_params = kwargs.get("litellm_params", {})
        optional_params = kwargs.get("optional_params", {})
        
        # Debug logging (Commented out to reduce noise)
        # logger.info(f"[PrometheusRouter] acompletion called. stream={stream}, litellm_params={litellm_params}, optional_params={optional_params}")
        
        # 만약 stream=True인데 여기로 왔다면, 강제로 astreaming으로 넘겨야 하는데
        # acompletion에서 generator를 반환하면 LiteLLM 로깅에서 에러가 발생함.
        # 따라서 여기서는 어쩔 수 없이 동기 처리를 하거나, 로깅을 패치해야 함.
        # 일단은 동기(블로킹)으로 처리하여 에러를 방지함.
        if stream:
             logger.warning("[PrometheusRouter] Stream requested but acompletion called. Returning full response (Streaming disabled due to interface mismatch).")

        # 간단하게 동기 메서드 호출 (비동기 최적화 생략)
        return self.completion(*args, **kwargs)
    
    async def astreaming(self, *args, **kwargs) -> AsyncIterator[GenericStreamingChunk]:
        """비동기 스트리밍 (Real Streaming)."""
        start_ts = time.time()
        start_dt = datetime.fromtimestamp(start_ts).strftime("%H:%M:%S.%f")[:-3]
        
        # model parameter extraction for logging
        model = kwargs.get("model", "")
        if not model:
            # try to find in params
            litellm_params = kwargs.get("litellm_params", {})
            model = litellm_params.get("model", "unknown")
            
        # Extract Trace ID
        # We pass it via extra_body={"metadata": {"trace_id": ...}} from backend
        litellm_params = kwargs.get("litellm_params", {})
        metadata = litellm_params.get("metadata", {}) or {}
        trace_id = metadata.get("trace_id", "unknown")
            
        def get_log_prefix():
            return f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}][{trace_id}]"

        logger.info(f"{get_log_prefix()} [PrometheusRouter] Request START: {model}")
        
        messages = kwargs.get("messages", [])
        api_base, selected_model, provider = await select_provider_async()
        
        # Provider selection time
        selection_latency = time.time() - start_ts
        logger.debug(f"{get_log_prefix()} [PrometheusRouter] Provider selected: {provider} (Latency: {selection_latency:.3f}s)")
        
        url = f"{api_base}/v1/chat/completions"
        payload = {"model": selected_model, "messages": messages, "stream": True}
        
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

    async def transcription(self, *args, **kwargs) -> ModelResponse:
        """Audio Transcription.

        Architecture V4: 요청된 모델에 따라 라우팅 결정
        - whisper-large-v3: GPU (Audio Gateway) - insanely-fast-whisper-rocm (정확도)
        - whisper-turbo: GPU (Audio Gateway) - whisper.cpp turbo (속도 폴백)
        - flm-audio: NPU (FLM Server) 우선, 실패 시 GPU whisper-turbo로 폴백
        """
        # Prepare multipart/form-data
        files = kwargs.get("files", {})
        data = kwargs.get("data", {})

        # 요청된 모델 확인
        requested_model = data.get("model", "")

        # 모델명에 따라 라우팅 결정
        # whisper-large-v3 → GPU (insanely-fast-whisper, 정확도 모드)
        # whisper-turbo → GPU (whisper.cpp turbo, 속도 모드 폴백용)
        # flm-audio → NPU 우선, 실패 시 GPU whisper-turbo로 폴백
        is_accuracy_mode = requested_model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3")
        is_speed_mode = requested_model in ("flm-audio", "flm")
        is_turbo_mode = requested_model in ("whisper-turbo", "whisper-large-v3-turbo", "turbo")

        if is_accuracy_mode:
            # 정확도 모드: GPU Audio Gateway (insanely-fast-whisper-rocm)
            force_provider = "gpu"
            target_model = "whisper-large-v3"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> GPU (insanely-fast-whisper, accuracy)")
        elif is_turbo_mode:
            # 터보 모드: GPU Audio Gateway (whisper.cpp turbo)
            force_provider = "gpu"
            target_model = "whisper-turbo"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> GPU (whisper.cpp turbo)")
        elif is_speed_mode:
            # 속도 모드: NPU 우선
            force_provider = "npu"
            target_model = "flm-audio"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> NPU (FLM, speed)")
        else:
            # 기본값: 정확도 모드
            force_provider = "gpu"
            target_model = "whisper-large-v3"
            logger.info(f"[PrometheusRouter] Model '{requested_model}' -> Default GPU (accuracy)")

        api_base, model, provider = await select_provider_async(
            task_type="audio",
            force_provider=force_provider,
        )

        logger.info(f"[PrometheusRouter] Transcribing with {provider} (api_base={api_base})")

        # NOTE: endpoint depends on the provider's API. Assuming OpenAI compatible.
        url = f"{api_base}/v1/audio/transcriptions"

        # Model override
        data["model"] = target_model

        # Remove "file" from data if it exists, as it's in files
        if "file" in data:
            del data["file"]

        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                response = await client.post(url, data=data, files=files)
                response.raise_for_status()
                result = response.json()
        except Exception as e:
            # 속도 모드에서 NPU 실패 시 GPU whisper-turbo로 폴백
            if is_speed_mode:
                logger.warning(f"[PrometheusRouter] NPU transcription failed: {e}, falling back to GPU whisper-turbo")

                # GPU Audio Gateway로 폴백 (whisper.cpp turbo)
                api_base, model, provider = await select_provider_async(
                    task_type="audio",
                    force_provider="gpu",
                )
                url = f"{api_base}/v1/audio/transcriptions"
                data["model"] = "whisper-turbo"  # whisper.cpp turbo 사용

                logger.info(f"[PrometheusRouter] Fallback to {provider} with whisper-turbo")

                try:
                    async with httpx.AsyncClient(timeout=1800.0) as client:
                        response = await client.post(url, data=data, files=files)
                        response.raise_for_status()
                        result = response.json()
                except Exception as fallback_error:
                    logger.error(f"[PrometheusRouter] Fallback transcription also failed: {fallback_error}")
                    raise fallback_error
            else:
                logger.error(f"[PrometheusRouter] Transcription failed: {e}")
                raise e

        return ModelResponse(
            id=result.get("id", f"transcribe-{uuid.uuid4()}"),
            created=int(time.time()),
            model=model,
            object="text",  # Transcription object
            choices=[
                {
                    "text": result.get("text", ""),
                    "segments": result.get("segments", []),
                    "language": result.get("language", ""),
                }
            ],
        )

# LiteLLM에 등록할 핸들러 인스턴스
prometheus_router = PrometheusRouter()
