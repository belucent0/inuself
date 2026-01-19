"""Stream Processor - Redis Stream 기반 GPU/NPU 작업 처리 + Control API.

Architecture V7.3: 메시징 기반 아키텍처 + 프로바이더 프로세스 통합 관리
- Docker (Worker/LiteLLM) -> Redis Stream -> Provider Manager (Host) -> GPU/NPU Servers (localhost)
- Docker -> Host HTTP 통신 제거로 Docker Desktop 크래시 방지
- ProviderManager가 모든 프로바이더 프로세스 직접 관리

Streams:
- stream:gpu:requests  - GPU 작업 요청 (Worker -> Provider Manager)
- stream:gpu:responses - GPU 작업 응답 (Provider Manager -> Worker)
- stream:provider:requests  - Control API 요청 (외부 -> Provider Manager)
- stream:provider:responses - Control API 응답 (Provider Manager -> 외부)
"""

import os
import json
import time
import asyncio
import logging
import uuid
import base64
from typing import Optional, Dict, Any

import httpx
import redis.asyncio as redis_async

from core.config import settings
from core.manager import ProviderManager
from services.job_tracker import JobTracker, JobStatus
from services.provider_service import ProviderService

logger = logging.getLogger("StreamProcessor")


class StreamProcessor:
    """Redis Stream 기반 GPU 작업 처리 + Control API.

    V7.3: ProviderManager 통합으로 모든 프로바이더 프로세스 직접 관리.
    - GPU 작업 스트림 (stream:gpu:*) 처리
    - Control API 스트림 (stream:provider:*) 처리
    - 작업 추적 (JobTracker)
    """

    # Task type -> Provider 매핑
    TASK_PROVIDER_MAP = {
        "diarization": "diarization",
        "transcription": "whisper-cpp",  # 기본값, 모델에 따라 변경
        "llm_completion": "llama-server",  # 기본값, 모델에 따라 변경
        "ocr": "llama-ocr-server",  # 기본값, 모델에 따라 변경
    }

    def __init__(self, provider_manager: ProviderManager = None):
        self.redis: Optional[redis_async.Redis] = None
        self.is_running = True
        self.http_client: Optional[httpx.AsyncClient] = None
        self.provider_manager = provider_manager or ProviderManager()
        self.job_tracker: Optional[JobTracker] = None
        self._service: Optional[ProviderService] = None

    @property
    def service(self) -> ProviderService:
        """ProviderService 인스턴스 (지연 생성)."""
        if self._service is None:
            self._service = ProviderService(self.provider_manager, self.job_tracker)
        return self._service

    async def connect(self):
        """Redis 연결 및 Consumer Group 생성."""
        # 프로바이더 프로세스 시작
        logger.info("Starting all provider processes...")
        await self.provider_manager.start_all_providers()

        self.redis = redis_async.from_url(settings.redis_url, decode_responses=True)
        self.http_client = httpx.AsyncClient(timeout=settings.default_timeout)

        # JobTracker 초기화
        self.job_tracker = JobTracker(self.redis)
        logger.info("JobTracker initialized")

        # GPU 작업 Consumer Group 생성
        try:
            await self.redis.xgroup_create(
                settings.request_stream,
                settings.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info(f"Created GPU consumer group '{settings.consumer_group}'")
        except redis_async.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"GPU consumer group '{settings.consumer_group}' already exists")
            else:
                raise

        # Control API Consumer Group 생성
        try:
            await self.redis.xgroup_create(
                settings.control_request_stream,
                settings.control_consumer_group,
                id="0",
                mkstream=True
            )
            logger.info(f"Created Control consumer group '{settings.control_consumer_group}'")
        except redis_async.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Control consumer group '{settings.control_consumer_group}' already exists")
            else:
                raise

        logger.info(f"Connected to Redis. Consumer: {settings.consumer_name}")

    async def close(self):
        """리소스 정리."""
        logger.info("Stopping all provider processes...")
        await self.provider_manager.stop_all_providers()

        if self.http_client:
            await self.http_client.aclose()
        if self.redis:
            await self.redis.aclose()

    async def publish_response(self, request_id: str, result: dict):
        """결과를 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "result": json.dumps(result),
            "timestamp": time.time(),
        }
        await self.redis.xadd(settings.response_stream, response)
        logger.info(f"Published response for request_id={request_id}")

    async def publish_error(self, request_id: str, error: str):
        """에러를 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "error": error,
            "timestamp": time.time(),
        }
        await self.redis.xadd(settings.response_stream, response)
        logger.error(f"Published error for request_id={request_id}: {error}")

    # ==========================================
    # Control API Methods
    # ==========================================

    async def publish_control_response(self, request_id: str, result: Dict[str, Any]):
        """Control API 응답을 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "result": json.dumps(result),
            "timestamp": str(time.time()),
        }
        await self.redis.xadd(settings.control_response_stream, response)
        logger.info(f"Published control response for request_id={request_id}")

    async def publish_control_error(self, request_id: str, error: str):
        """Control API 에러를 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "error": error,
            "timestamp": str(time.time()),
        }
        await self.redis.xadd(settings.control_response_stream, response)
        logger.error(f"Published control error for request_id={request_id}: {error}")

    async def handle_control_list_providers(self, request_id: str, data: dict):
        """프로바이더 목록 조회."""
        try:
            result = await self.service.list_providers()
            await self.publish_control_response(request_id, {
                "action": "list_providers",
                **result,
            })
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_get_status(self, request_id: str, data: dict):
        """전체 상태 조회."""
        try:
            result = await self.service.get_status()
            await self.publish_control_response(request_id, {
                "action": "get_status",
                **result,
            })
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_get_jobs(self, request_id: str, data: dict):
        """작업 목록 조회."""
        try:
            provider = data.get("provider")
            trace_id = data.get("trace_id")
            result = await self.service.get_jobs(provider=provider, trace_id=trace_id)
            await self.publish_control_response(request_id, {
                "action": "get_jobs",
                **result,
            })
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_load_provider(self, request_id: str, data: dict):
        """프로바이더 로드."""
        try:
            name = data.get("name")
            if not name:
                await self.publish_control_error(request_id, "Provider name required")
                return
            result = await self.service.load_provider(name)
            await self.publish_control_response(request_id, {"action": "load", **result})
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_unload_provider(self, request_id: str, data: dict):
        """프로바이더 언로드."""
        try:
            name = data.get("name")
            if not name:
                await self.publish_control_error(request_id, "Provider name required")
                return
            result = await self.service.unload_provider(name)
            await self.publish_control_response(request_id, {"action": "unload", **result})
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_reload_provider(self, request_id: str, data: dict):
        """프로바이더 재로드."""
        try:
            name = data.get("name")
            if not name:
                await self.publish_control_error(request_id, "Provider name required")
                return
            result = await self.service.reload_provider(name)
            await self.publish_control_response(request_id, {"action": "reload", **result})
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_start_all(self, request_id: str, data: dict):
        """모든 프로바이더 시작."""
        try:
            result = await self.service.start_all()
            await self.publish_control_response(request_id, {"action": "start_all", **result})
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_stop_all(self, request_id: str, data: dict):
        """모든 프로바이더 중지."""
        try:
            result = await self.service.stop_all()
            await self.publish_control_response(request_id, {"action": "stop_all", **result})
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def process_control_message(self, message_id: str, data: dict):
        """Control API 메시지 처리."""
        request_id = data.get("request_id", str(uuid.uuid4()))
        action = data.get("action")

        logger.info(f"Processing control message: id={message_id}, action={action}, request_id={request_id}")

        try:
            if action == "list_providers":
                await self.handle_control_list_providers(request_id, data)
            elif action == "get_status":
                await self.handle_control_get_status(request_id, data)
            elif action == "get_jobs":
                await self.handle_control_get_jobs(request_id, data)
            elif action == "load":
                await self.handle_control_load_provider(request_id, data)
            elif action == "unload":
                await self.handle_control_unload_provider(request_id, data)
            elif action == "reload":
                await self.handle_control_reload_provider(request_id, data)
            elif action == "start_all":
                await self.handle_control_start_all(request_id, data)
            elif action == "stop_all":
                await self.handle_control_stop_all(request_id, data)
            else:
                await self.publish_control_error(request_id, f"Unknown action: {action}")

            await self.redis.xack(
                settings.control_request_stream,
                settings.control_consumer_group,
                message_id
            )
        except Exception as e:
            logger.exception(f"Error processing control message {message_id}: {e}")
            await self.publish_control_error(request_id, str(e))
            await self.redis.xack(
                settings.control_request_stream,
                settings.control_consumer_group,
                message_id
            )

    # ==========================================
    # GPU Task Handlers
    # ==========================================

    def _get_provider_for_task(self, task_type: str, data: dict) -> str:
        """작업 유형과 데이터에 따라 사용할 프로바이더 결정."""
        if task_type == "transcription":
            model = data.get("model", "whisper-turbo")
            if model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3"):
                return "insanely-fast"
            elif model in ("flm-audio", "flm", "openai/flm-audio"):
                return "flm-asr"
            return "whisper-cpp"

        elif task_type == "llm_completion":
            model = data.get("model", "")
            target_server = data.get("target_server", "auto")
            if target_server in ("flm", "npu"):
                return "flm-llm"
            elif target_server in ("llama", "gpu"):
                return "llama-server"
            elif "flm" in model or "npu" in model.lower():
                return "flm-llm"
            return "llama-server"

        elif task_type == "ocr":
            accuracy_mode = data.get("accuracy_mode", "speed")
            if accuracy_mode == "speed":
                return "flm-ocr"
            return "llama-ocr-server"

        return self.TASK_PROVIDER_MAP.get(task_type, "unknown")

    async def handle_diarization(self, request_id: str, data: dict):
        """Diarization 작업 처리."""
        file_content_b64 = data.get("file_content")
        min_speakers = data.get("min_speakers")
        max_speakers = data.get("max_speakers")

        logger.info(f"[{request_id}] Starting diarization...")

        try:
            url = f"{settings.diarization_url}/v1/audio/diarization"
            file_bytes = base64.b64decode(file_content_b64)

            files = {"file": ("audio.wav", file_bytes, "audio/wav")}
            form_data = {"model": "pyannote"}
            if min_speakers:
                form_data["min_speakers"] = str(min_speakers)
            if max_speakers:
                form_data["max_speakers"] = str(max_speakers)

            response = await self.http_client.post(url, files=files, data=form_data)
            response.raise_for_status()
            result = response.json()

            logger.info(f"[{request_id}] Diarization completed: {len(result.get('segments', []))} segments")
            await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            await self.publish_error(request_id, f"Diarization timeout")
        except httpx.HTTPStatusError as e:
            await self.publish_error(request_id, f"Diarization HTTP error: {e.response.status_code}")
        except Exception as e:
            await self.publish_error(request_id, f"Diarization failed: {str(e)}")

    async def handle_transcription(self, request_id: str, data: dict):
        """ASR Transcription 작업 처리."""
        file_content_b64 = data.get("file_content")
        model = data.get("model", "whisper-turbo")
        language = data.get("language")

        logger.info(f"[{request_id}] Starting transcription (model={model})...")

        try:
            # 모델에 따라 URL 선택
            if model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3"):
                url = f"{settings.insanely_fast_url}/v1/audio/transcriptions"
                actual_model = "whisper-large-v3"
            elif model in ("flm-audio", "flm", "openai/flm-audio"):
                url = f"{settings.flm_asr_url}/v1/audio/transcriptions"
                actual_model = "flm-audio"
            else:
                url = f"{settings.whisper_cpp_url}/v1/audio/transcriptions"
                actual_model = "whisper-turbo"

            file_bytes = base64.b64decode(file_content_b64)

            files = {"file": ("audio.wav", file_bytes, "audio/wav")}
            form_data = {"model": actual_model}
            if language:
                form_data["language"] = language

            response = await self.http_client.post(url, files=files, data=form_data)
            response.raise_for_status()
            result = response.json()

            logger.info(f"[{request_id}] Transcription completed")
            await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            await self.publish_error(request_id, f"Transcription timeout")
        except httpx.HTTPStatusError as e:
            await self.publish_error(request_id, f"Transcription HTTP error: {e.response.status_code}")
        except Exception as e:
            await self.publish_error(request_id, f"Transcription failed: {str(e)}")

    async def handle_llm_completion(self, request_id: str, data: dict):
        """LLM Completion 작업 처리."""
        messages_raw = data.get("messages", "[]")
        messages = json.loads(messages_raw) if isinstance(messages_raw, str) else messages_raw
        model = data.get("model", "qwen3-4b")
        max_tokens = int(data.get("max_tokens", 4096))
        temperature = float(data.get("temperature", 0.7))
        target_server = data.get("target_server", "auto")

        logger.info(f"[{request_id}] Starting LLM completion (model={model}, target={target_server})...")

        try:
            # 서버 선택
            use_npu = False
            if target_server in ("flm", "npu"):
                use_npu = True
            elif target_server in ("llama", "gpu"):
                use_npu = False
            elif "flm" in model or "npu" in model.lower():
                use_npu = True
            elif any(p in model.lower() for p in ["qwen", "gemma", "deepseek", "phi", "lfm"]) and ":" in model:
                use_npu = True

            if use_npu:
                logger.info(f"[{request_id}] Using FLM LLM server (NPU)...")
                url = f"{settings.flm_llm_url}/v1/chat/completions"
                actual_model = "lfm2:2.6b"
            else:
                logger.info(f"[{request_id}] Using llama-server (GPU)...")
                url = f"{settings.llama_server_url}/v1/chat/completions"
                actual_model = model

            payload = {
                "model": actual_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }

            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            logger.info(f"[{request_id}] LLM completion completed")
            await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            await self.publish_error(request_id, f"LLM completion timeout")
        except httpx.HTTPStatusError as e:
            await self.publish_error(request_id, f"LLM HTTP error: {e.response.status_code}")
        except Exception as e:
            await self.publish_error(request_id, f"LLM completion failed: {str(e)}")

    async def handle_ocr(self, request_id: str, data: dict):
        """OCR Vision 작업 처리."""
        image_content_b64 = data.get("image_content")
        model = data.get("model", "qwen3vl-4b")
        prompt = data.get("prompt", "Extract all text from this image.")
        accuracy_mode = data.get("accuracy_mode", "speed")

        logger.info(f"[{request_id}] Starting OCR (model={model}, mode={accuracy_mode})...")

        try:
            use_npu = accuracy_mode == "speed"

            if use_npu:
                logger.info(f"[{request_id}] Using FLM OCR server (NPU)...")
                url = f"{settings.flm_ocr_url}/v1/chat/completions"
                ocr_model = "qwen3vl-it:4b"
            else:
                logger.info(f"[{request_id}] Using llama-ocr-server (GPU)...")
                url = f"{settings.llama_ocr_server_url}/v1/chat/completions"
                ocr_model = model if model != "qwen3vl-4b" else "qwen3-vl"

            payload = {
                "model": ocr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_content_b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 4096,
                "temperature": 0.1,
            }

            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            ocr_text = ""
            if "choices" in result and len(result["choices"]) > 0:
                ocr_text = result["choices"][0].get("message", {}).get("content", "")

            logger.info(f"[{request_id}] OCR completed ({len(ocr_text)} chars)")
            result["text"] = ocr_text
            await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            await self.publish_error(request_id, f"OCR timeout")
        except httpx.HTTPStatusError as e:
            await self.publish_error(request_id, f"OCR HTTP error: {e.response.status_code}")
        except Exception as e:
            logger.exception(f"[{request_id}] OCR failed")
            await self.publish_error(request_id, f"OCR failed: {str(e)}")

    async def handle_health_check(self, request_id: str, data: dict):
        """Health check 작업 처리."""
        service = data.get("service", "all")

        results = {}
        services_to_check = {
            "diarization": f"{settings.diarization_url}/health",
            "whisper-cpp": f"{settings.whisper_cpp_url}/health",
            "insanely-fast": f"{settings.insanely_fast_url}/health",
            "llama": f"{settings.llama_server_url}/health",
            "llama-ocr": f"{settings.llama_ocr_server_url}/health",
            "flm-asr": f"{settings.flm_asr_url}/v1/models",
            "flm-llm": f"{settings.flm_llm_url}/v1/models",
            "flm-ocr": f"{settings.flm_ocr_url}/v1/models",
        }

        if service != "all":
            services_to_check = {service: services_to_check.get(service)}

        for svc_name, url in services_to_check.items():
            if not url:
                continue
            try:
                response = await self.http_client.get(url, timeout=5.0)
                results[svc_name] = {
                    "status": "healthy" if response.status_code == 200 else "unhealthy",
                    "code": response.status_code,
                }
            except Exception as e:
                results[svc_name] = {"status": "unreachable", "error": str(e)}

        await self.publish_response(request_id, results)

    # ==========================================
    # Main Loop
    # ==========================================

    async def process_message(self, message_id: str, data: dict):
        """GPU 작업 메시지 처리 (JobTracker 통합)."""
        request_id = data.get("request_id", str(uuid.uuid4()))
        task_type = data.get("type")
        trace_id = data.get("trace_id")  # 클라이언트에서 전달된 TraceId

        log_trace = f", trace_id={trace_id}" if trace_id else ""
        logger.info(f"Processing message: id={message_id}, type={task_type}, request_id={request_id}{log_trace}")

        # 프로바이더 결정
        provider = self._get_provider_for_task(task_type, data)

        # 작업 시작 기록
        job = None
        if self.job_tracker and task_type != "health_check":
            job = await self.job_tracker.start_job(
                job_id=message_id,
                request_id=request_id,
                task_type=task_type,
                provider=provider,
                metadata={"model": data.get("model")},
                trace_id=trace_id,
            )

        success = True
        error_msg = None

        try:
            if task_type == "diarization":
                await self.handle_diarization(request_id, data)
            elif task_type == "transcription":
                await self.handle_transcription(request_id, data)
            elif task_type == "llm_completion":
                await self.handle_llm_completion(request_id, data)
            elif task_type == "ocr":
                await self.handle_ocr(request_id, data)
            elif task_type == "health_check":
                await self.handle_health_check(request_id, data)
            else:
                await self.publish_error(request_id, f"Unknown task type: {task_type}")
                success = False
                error_msg = f"Unknown task type: {task_type}"

            await self.redis.xack(settings.request_stream, settings.consumer_group, message_id)

        except Exception as e:
            logger.exception(f"Error processing message {message_id}: {e}")
            await self.publish_error(request_id, str(e))
            await self.redis.xack(settings.request_stream, settings.consumer_group, message_id)
            success = False
            error_msg = str(e)

        # 작업 완료 기록
        if job and self.job_tracker:
            await self.job_tracker.complete_job(message_id, success=success, error=error_msg)

    async def run(self):
        """메인 루프 - GPU 작업 + Control API 스트림 동시 처리."""
        await self.connect()

        logger.info("Provider Manager started. Waiting for messages...")
        logger.info(f"  GPU Stream: {settings.request_stream}")
        logger.info(f"  Control Stream: {settings.control_request_stream}")

        while self.is_running:
            try:
                # 양쪽 스트림 동시 읽기
                messages = await self.redis.xreadgroup(
                    settings.consumer_group,
                    settings.consumer_name,
                    {
                        settings.request_stream: ">",
                        settings.control_request_stream: ">",
                    },
                    count=5,
                    block=5000,
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            # 스트림에 따라 처리 분기
                            if stream_name == settings.request_stream:
                                await self.process_message(message_id, message_data)
                            elif stream_name == settings.control_request_stream:
                                await self.process_control_message(message_id, message_data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(1)

        await self.close()
        logger.info("Provider Manager stopped.")

    def shutdown(self):
        """종료 요청."""
        logger.info("Shutdown requested...")
        self.is_running = False
