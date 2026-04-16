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
from core.telemetry import (
    trace_gpu_operation,
    record_operation_result,
    trace_provider_load,
    trace_http_request,
    trace_response_publish,
)
from services.job_tracker import JobTracker, JobStatus
from services.provider_service import ProviderService

# Tier 라우팅 설정 (공통 모듈에서 import)
# infra/ 디렉토리를 path에 추가하여 shared 모듈 접근
import sys
_infra_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _infra_dir not in sys.path:
    sys.path.insert(0, _infra_dir)
from shared.tier_config import TIER_MODEL_MAP, resolve_tier_to_model

# TYPE_CHECKING으로 순환 임포트 방지
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.idle_manager import IdleTimeoutManager

logger = logging.getLogger("StreamProcessor")


class StreamProcessor:
    """Redis Stream 기반 GPU 작업 처리 + Control API.

    V7.3: ProviderManager 통합으로 모든 프로바이더 프로세스 직접 관리.
    V7.4: IdleTimeoutManager 연동으로 On-Demand 프로바이더 자동 언로드.
    - GPU 작업 스트림 (stream:gpu:*) 처리
    - Control API 스트림 (stream:provider:*) 처리
    - 작업 추적 (JobTracker)
    - Idle timeout 관리 (On-Demand 프로바이더 자동 언로드)
    """

    # Task type -> Provider 매핑
    TASK_PROVIDER_MAP = {
        "diarization": "diarization",
        "transcription": "whisper-cpp",  # 기본값, 모델에 따라 변경
        "llm_completion": "llama-server",  # 기본값, 모델에 따라 변경
        "ocr": "llama-ocr-server",  # 기본값, 모델에 따라 변경
    }

    # ==========================================
    # Tier-based Model Routing (공통 모듈 사용)
    # ==========================================
    # TIER_MODEL_MAP은 infra/shared/tier_config.py에서 import됨
    # 모든 티어 관련 수정은 tier_config.py에서 하세요.

    # Provider -> Device Group 매핑 (동시성 제한용)
    PROVIDER_DEVICE_GROUP = {
        # GPU 프로바이더
        "diarization-server": "gpu",
        "whisper-server": "gpu",
        "insanely-fast-server": "gpu",
        "lemonade-server": "gpu",  # tier-recap 전용
        "llama-server": "gpu",
        "llama-ocr-server": "gpu",
        # NPU 프로바이더
        "flm-asr": "npu",
        "flm-llm": "npu",  # tier-simple
        "flm-llm-thinking": "npu",  # tier-thinking
        "flm-ocr": "npu",
    }

    def __init__(
        self,
        provider_manager: ProviderManager = None,
        idle_manager: "IdleTimeoutManager" = None,
    ):
        self.redis: Optional[redis_async.Redis] = None
        self.is_running = True
        self.http_client: Optional[httpx.AsyncClient] = None
        self.provider_manager = provider_manager or ProviderManager()
        self.idle_manager = idle_manager  # On-Demand 프로바이더 자동 언로드용
        self.job_tracker: Optional[JobTracker] = None
        self._service: Optional[ProviderService] = None

        # Device Group별 세마포어 (동시 실행 제한)
        # GPU/NPU가 각각 하나씩이므로 동일 device group 내에서는 순차 실행
        self._device_semaphores: Dict[str, asyncio.Semaphore] = {
            "gpu": asyncio.Semaphore(settings.gpu_max_concurrent),
            "npu": asyncio.Semaphore(settings.npu_max_concurrent),
        }
        self._pending_jobs: Dict[str, int] = {
            "gpu": 0,
            "npu": 0,
        }  # 대기 중인 작업 수 추적

        # lemonade 모델 warm-up/recovery 직렬화 (동시 warm-up 방지)
        self._lemonade_model_lock = asyncio.Lock()
        self._lemonade_recovery_task: Optional[asyncio.Task] = None

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

        # 모든 GPU 작업 스트림에 Consumer Group 생성
        gpu_streams = [
            settings.request_stream,  # legacy stream (chat)
            settings.media_request_stream,  # audio/vision
            settings.chat_request_stream,  # chat completions
            settings.recap_request_stream,  # summaries
        ]
        # 중복 제거
        gpu_streams = list(dict.fromkeys(gpu_streams))

        for stream in gpu_streams:
            try:
                await self.redis.xgroup_create(
                    stream, settings.consumer_group, id="0", mkstream=True
                )
                logger.info(f"Created GPU consumer group '{settings.consumer_group}' for {stream}")
            except redis_async.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    logger.info(
                        f"GPU consumer group '{settings.consumer_group}' already exists for {stream}"
                    )
                else:
                    raise

        # Control API Consumer Group 생성
        try:
            await self.redis.xgroup_create(
                settings.control_request_stream,
                settings.control_consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                f"Created Control consumer group '{settings.control_consumer_group}'"
            )
        except redis_async.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(
                    f"Control consumer group '{settings.control_consumer_group}' already exists"
                )
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

    async def publish_chunk(
        self, request_id: str, chunk: str, finish_reason: Optional[str] = None
    ):
        """스트리밍 청크를 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "chunk": chunk,
            "timestamp": time.time(),
        }
        if finish_reason:
            response["finish_reason"] = finish_reason

        await self.redis.xadd(settings.response_stream, response)

    async def publish_error(self, request_id: str, error: str):
        """에러를 Response Stream에 발행."""
        response = {
            "request_id": request_id,
            "error": error,
            "timestamp": time.time(),
        }
        await self.redis.xadd(settings.response_stream, response)
        logger.error(f"Published error for request_id={request_id}: {error}")

    async def publish_processing_started(self, request_id: str, task_type: str, file_id: str = None):
        """작업 처리 시작 이벤트를 Response Stream에 발행.

        Provider가 실제로 GPU/NPU 서버에 요청을 보내기 직전에 호출되어
        Worker/Backend가 '인식 중' 상태로 변경할 수 있도록 합니다.

        Args:
            request_id: 요청 ID
            task_type: 작업 타입 (ocr, transcription, llm_completion 등)
            file_id: 파일 ID (선택적, Backend 상태 업데이트용)
        """
        response = {
            "request_id": request_id,
            "event": "processing_started",
            "task_type": task_type,
            "timestamp": time.time(),
        }
        if file_id:
            response["file_id"] = file_id

        # GPU Stream 응답 (LiteLLM/Worker용)
        await self.redis.xadd(settings.response_stream, response)

        # Worker Results Stream에도 발행 (Backend용)
        if file_id:
            # Backend StreamConsumer uses canonical types: asr/llm/ocr.
            worker_result_type = {
                "transcription": "asr",
                "llm_completion": "llm",
                "ocr": "ocr",
            }.get(task_type, task_type)
            worker_event = {
                "type": worker_result_type,
                "event": "processing_started",
                "file_id": file_id,
                "timestamp": time.time(),
            }
            await self.redis.xadd(settings.worker_result_stream, {"data": json.dumps(worker_event)})

        logger.info(f"Published processing_started event for request_id={request_id}, task_type={task_type}, file_id={file_id}")

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
            await self.publish_control_response(
                request_id,
                {
                    "action": "list_providers",
                    **result,
                },
            )
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_get_status(self, request_id: str, data: dict):
        """전체 상태 조회."""
        try:
            result = await self.service.get_status()
            await self.publish_control_response(
                request_id,
                {
                    "action": "get_status",
                    **result,
                },
            )
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_get_jobs(self, request_id: str, data: dict):
        """작업 목록 조회."""
        try:
            provider = data.get("provider")
            trace_id = data.get("trace_id")
            result = await self.service.get_jobs(provider=provider, trace_id=trace_id)
            await self.publish_control_response(
                request_id,
                {
                    "action": "get_jobs",
                    **result,
                },
            )
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
            await self.publish_control_response(
                request_id, {"action": "load", **result}
            )
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
            await self.publish_control_response(
                request_id, {"action": "unload", **result}
            )
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
            await self.publish_control_response(
                request_id, {"action": "reload", **result}
            )
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_start_all(self, request_id: str, data: dict):
        """모든 프로바이더 시작."""
        try:
            result = await self.service.start_all()
            await self.publish_control_response(
                request_id, {"action": "start_all", **result}
            )
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def handle_control_stop_all(self, request_id: str, data: dict):
        """모든 프로바이더 중지."""
        try:
            result = await self.service.stop_all()
            await self.publish_control_response(
                request_id, {"action": "stop_all", **result}
            )
        except Exception as e:
            await self.publish_control_error(request_id, str(e))

    async def process_control_message(self, message_id: str, data: dict):
        """Control API 메시지 처리."""
        request_id = data.get("request_id", str(uuid.uuid4()))
        action = data.get("action")

        logger.info(
            f"Processing control message: id={message_id}, action={action}, request_id={request_id}"
        )

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
                await self.publish_control_error(
                    request_id, f"Unknown action: {action}"
                )

            await self.redis.xack(
                settings.control_request_stream,
                settings.control_consumer_group,
                message_id,
            )
        except Exception as e:
            logger.exception(f"Error processing control message {message_id}: {e}")
            await self.publish_control_error(request_id, str(e))
            await self.redis.xack(
                settings.control_request_stream,
                settings.control_consumer_group,
                message_id,
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

            # Tier 기반 라우팅: tier-* 패턴이면 실제 모델로 변환
            if model.startswith("tier-"):
                resolved_model = resolve_tier_to_model(model)
                model = resolved_model

            # Thinking model 감지 (-tk 접미사: qwen3-tk, lfm2.5-tk, gpt-oss-sg-tk 등)
            is_thinking_model = model and "-tk" in model

            # 명시적 target_server 지정 시 우선
            if target_server in ("flm", "npu"):
                return "flm-llm-thinking" if is_thinking_model else "flm-llm"
            elif target_server in ("llama", "gpu"):
                return "llama-server"

            # 패턴 기반 NPU 모델 감지 (확장성: 새 모델 추가 시 접두사만 추가)
            # FLM에서 지원하는 모델 패턴: lfm, qwen, gpt-oss, exaone, gemma, deepseek, phi 등
            NPU_MODEL_PREFIXES = (
                "lfm",
                "qwen",
                "gpt-oss",
                "exaone",
                "gemma",
                "deepseek",
                "phi",
                "llama-flm",
                "mistral-flm",
            )

            model_lower = model.lower()

            # ":" 포함 (버전 지정) + NPU 접두사 매칭 → NPU로 라우팅
            if ":" in model and any(
                model_lower.startswith(p) for p in NPU_MODEL_PREFIXES
            ):
                return "flm-llm-thinking" if is_thinking_model else "flm-llm"

            # 명시적 flm/npu 키워드
            if "flm" in model_lower or "npu" in model_lower:
                return "flm-llm-thinking" if is_thinking_model else "flm-llm"

            # 기본값: GPU llama-server
            return "llama-server"

        elif task_type == "ocr":
            accuracy_mode = data.get("accuracy_mode", "speed")
            if accuracy_mode == "speed":
                return "flm-ocr"
            return "llama-ocr-server"

        return self.TASK_PROVIDER_MAP.get(task_type, "unknown")

    async def _ensure_provider_ready(
        self, provider_name: str, timeout: float = 120.0
    ) -> bool:
        """On-Demand 프로바이더가 준비될 때까지 대기 (필요시 자동 로드).

        Args:
            provider_name: 프로바이더 이름 (e.g., "insanely-fast-server")
            timeout: 최대 대기 시간 (초)

        Returns:
            준비 완료 여부
        """
        from core.manager import ProviderStatus

        state = self.provider_manager.provider_states.get(provider_name)
        if not state:
            logger.warning(f"Provider {provider_name} not found in states")
            return False

        # 이미 UP 상태면 바로 반환
        if state.status == ProviderStatus.UP:
            return True

        # On-Demand 로딩 span으로 감싸기
        with trace_provider_load(provider_name, on_demand=True) as span:
            # DOWN/COOLDOWN 상태면 로드 시도
            if state.status in (ProviderStatus.DOWN, ProviderStatus.COOLDOWN):
                logger.info(f"[On-Demand] Loading {provider_name}...")
                success = await self.provider_manager.load_provider(provider_name)
                if not success:
                    logger.error(f"[On-Demand] Failed to load {provider_name}")
                    if span:
                        span.set_attribute("load.success", False)
                        span.set_attribute("load.error", "Failed to start provider")
                    return False

            # STARTING 상태면 UP 될 때까지 대기
            import asyncio

            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < timeout:
                state = self.provider_manager.provider_states.get(provider_name)
                if state and state.status == ProviderStatus.UP:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    logger.info(f"[On-Demand] {provider_name} is ready")
                    if span:
                        span.set_attribute("load.success", True)
                        span.set_attribute("load.elapsed_ms", elapsed * 1000)
                    return True
                await asyncio.sleep(1)

            if span:
                span.set_attribute("load.success", False)
                span.set_attribute("load.error", f"Timeout after {timeout}s")
            logger.error(
                f"[On-Demand] {provider_name} failed to become ready within {timeout}s"
            )
            return False

    async def _ensure_lemonade_model_ready(self, timeout: float = 600.0) -> bool:
        """lemonade-server 프로세스 UP + 모델 추론 가능까지 보장.

        _ensure_provider_ready()는 프로세스 레벨만 확인하지만,
        이 메서드는 실제 모델이 GPU에 로드되어 추론 가능한지까지 검증한다.
        항시 구동 / 온디맨드 모드 모두에서 동작.

        Args:
            timeout: 전체 대기 타임아웃 (초)

        Returns:
            모델 준비 완료 여부
        """
        from core.manager import _load_env_vars

        # Step 1: 프로세스 UP 확인 (기존 온디맨드 로직)
        if not await self._ensure_provider_ready("lemonade-server", timeout=timeout):
            return False

        provider = self.provider_manager.get_provider_config("lemonade-server")
        if not provider:
            logger.error("[lemonade-model] Provider config not found")
            return False

        env_vars = _load_env_vars()
        model_name = env_vars.get("LEMONADE_SUMMARIZE_MODEL", "gpt-oss-20b-mxfp4-GGUF")

        # Step 2: 빠른 probe - 모델이 이미 로드되어 있으면 바로 통과
        if await self.provider_manager._probe_lemonade_model(provider.port, model_name):
            return True

        # Step 3-5: warm-up/recovery (동시 요청 직렬화)
        async with self._lemonade_model_lock:
            # 락 획득 후 다시 probe (다른 요청이 이미 로딩 완료했을 수 있음)
            if await self.provider_manager._probe_lemonade_model(provider.port, model_name):
                return True

            # Step 3: warm-up 시도 (모델 cold start 로딩)
            logger.info("[lemonade-model] Model not ready, attempting warm-up...")
            warmup_ok = await self.provider_manager._warm_up_lemonade(provider.port, model_name)

            if warmup_ok:
                # warm-up 성공 후 probe로 최종 확인
                if await self.provider_manager._probe_lemonade_model(provider.port, model_name):
                    return True

            # Step 4: warm-up 실패 → provider 재시작 (llamacpp backend 죽음 등)
            logger.warning("[lemonade-model] Warm-up failed, triggering provider recovery...")
            recovered = await self.provider_manager._recover_provider(provider)
            if not recovered:
                logger.error("[lemonade-model] Provider recovery failed")
                return False

            # Step 5: recovery 후 모델 로딩 대기 (probe 폴링)
            start_time = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - start_time < timeout:
                if await self.provider_manager._probe_lemonade_model(provider.port, model_name):
                    logger.info("[lemonade-model] Model ready after recovery")
                    return True
                await asyncio.sleep(10)

            logger.error(f"[lemonade-model] Model failed to become ready within {timeout}s")
            return False

    async def _background_lemonade_recovery(self) -> None:
        """lemonade 요청 실패 후 백그라운드에서 모델 상태 확인 + 복구.

        현재 요청은 이미 실패했으므로, 다음 요청이 성공할 수 있도록
        사전에 모델 상태를 점검하고 필요 시 recovery를 트리거한다.
        """
        try:
            from core.manager import _load_env_vars

            provider = self.provider_manager.get_provider_config("lemonade-server")
            if not provider:
                return

            env_vars = _load_env_vars()
            model_name = env_vars.get("LEMONADE_SUMMARIZE_MODEL", "gpt-oss-20b-mxfp4-GGUF")

            is_ok = await self.provider_manager._probe_lemonade_model(
                provider.port, model_name, timeout=15.0
            )
            if not is_ok:
                logger.warning(
                    "[lemonade-recovery] Model probe failed after request error, triggering recovery..."
                )
                await self.provider_manager._recover_provider(provider)
        except Exception as e:
            logger.error(f"[lemonade-recovery] Background recovery failed: {e}")

    async def handle_diarization(self, request_id: str, data: dict):
        """Diarization 작업 처리."""
        file_content_b64 = data.get("file_content")
        min_speakers = data.get("min_speakers")
        max_speakers = data.get("max_speakers")

        logger.info(f"[{request_id}] Starting diarization...")

        # On-Demand: diarization-server가 준비될 때까지 대기
        if not await self._ensure_provider_ready("diarization-server"):
            await self.publish_error(request_id, "Diarization server failed to start")
            return

        try:
            url = f"{settings.diarization_url}/v1/audio/diarization"
            file_bytes = base64.b64decode(file_content_b64)

            files = {"file": ("audio.wav", file_bytes, "audio/wav")}
            form_data = {"model": "pyannote"}
            if min_speakers:
                form_data["min_speakers"] = str(min_speakers)
            if max_speakers:
                form_data["max_speakers"] = str(max_speakers)

            # HTTP 요청 span
            with trace_http_request(
                "diarization-server", url, model="pyannote"
            ) as span:
                response = await self.http_client.post(url, files=files, data=form_data)
                response.raise_for_status()
                result = response.json()

                if span:
                    span.set_attribute("http.status_code", response.status_code)
                    span.set_attribute(
                        "response.segments_count", len(result.get("segments", []))
                    )

            logger.info(
                f"[{request_id}] Diarization completed: {len(result.get('segments', []))} segments"
            )

            # 응답 발행 span
            with trace_response_publish(request_id, success=True):
                await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"Diarization timeout")
        except httpx.HTTPStatusError as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(
                    request_id, f"Diarization HTTP error: {e.response.status_code}"
                )
        except Exception as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"Diarization failed: {str(e)}")

    async def handle_transcription(self, request_id: str, data: dict):
        """ASR Transcription 작업 처리."""
        file_content_b64 = data.get("file_content")
        model = data.get("model", "whisper-turbo")
        language = data.get("language")
        file_id = data.get("file_id")  # Worker에서 전달한 file_id

        logger.info(f"[{request_id}] Starting transcription (model={model}, file_id={file_id})...")

        # On-Demand: 모델에 따라 해당 서버 준비
        if model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3"):
            if not await self._ensure_provider_ready("insanely-fast-server"):
                await self.publish_error(
                    request_id, "Insanely-fast server failed to start"
                )
                return
        elif model in ("flm-audio", "flm", "openai/flm-audio"):
            # FLM ASR (NPU)
            if not await self._ensure_provider_ready("flm-asr"):
                await self.publish_error(request_id, "FLM ASR server failed to start")
                return
        else:
            # whisper-turbo (기본값) -> whisper-server 필요
            if not await self._ensure_provider_ready("whisper-server"):
                await self.publish_error(request_id, "Whisper server failed to start")
                return

        try:
            # 모델에 따라 URL 및 프로바이더 선택
            if model in ("whisper-large-v3", "whisper", "openai/whisper-large-v3"):
                url = f"{settings.insanely_fast_url}/v1/audio/transcriptions"
                actual_model = "whisper-large-v3"
                provider_name = "insanely-fast-server"
            elif model in ("flm-audio", "flm", "openai/flm-audio"):
                url = f"{settings.flm_asr_url}/v1/audio/transcriptions"
                actual_model = "flm-audio"
                provider_name = "flm-asr"
            else:
                url = f"{settings.whisper_cpp_url}/v1/audio/transcriptions"
                actual_model = "whisper-turbo"
                provider_name = "whisper-server"

            # Provider가 실제 작업을 시작하기 직전에 이벤트 발행
            await self.publish_processing_started(request_id, "transcription", file_id)

            file_bytes = base64.b64decode(file_content_b64)

            files = {"file": ("audio.wav", file_bytes, "audio/wav")}
            form_data = {"model": actual_model}
            if language:
                form_data["language"] = language

            # HTTP 요청 span
            with trace_http_request(provider_name, url, model=actual_model) as span:
                response = await self.http_client.post(url, files=files, data=form_data)
                response.raise_for_status()
                result = response.json()

                if span:
                    span.set_attribute("http.status_code", response.status_code)

            logger.info(f"[{request_id}] Transcription completed")

            # 응답 발행 span
            with trace_response_publish(request_id, success=True):
                await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"Transcription timeout")
        except httpx.HTTPStatusError as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(
                    request_id, f"Transcription HTTP error: {e.response.status_code}"
                )
        except Exception as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"Transcription failed: {str(e)}")

    async def handle_llm_completion(self, request_id: str, data: dict):
        """LLM Completion 작업 처리 (스트리밍 지원)."""
        messages_raw = data.get("messages", "[]")
        messages = (
            json.loads(messages_raw) if isinstance(messages_raw, str) else messages_raw
        )
        model = data.get("model", "tier-simple")
        max_tokens = int(data.get("max_tokens", 4096))
        temperature = float(data.get("temperature", 0.7))
        target_server = data.get("target_server", "auto")

        # Tier 기반 라우팅: tier-* 패턴이면 실제 모델로 변환
        original_tier = None
        if model.startswith("tier-"):
            original_tier = model
            model = resolve_tier_to_model(model)
            logger.info(f"[{request_id}] Tier routing: {original_tier} -> {model}")

        logger.info(
            f"[{request_id}] Starting LLM completion (model={model}, target={target_server})..."
        )

        # tier-recap은 lemonade-server (GPU) 전용
        use_lemonade = (original_tier == "tier-recap")

        # 서버 선택 로직 (On-Demand 로드 전에 결정)
        use_npu = False
        if use_lemonade:
            pass  # lemonade-server 고정
        elif target_server in ("flm", "npu"):
            use_npu = True
        elif target_server in ("llama", "gpu"):
            use_npu = False
        elif "flm" in model or "npu" in model.lower():
            use_npu = True
        elif (
            any(
                p in model.lower()
                for p in ["qwen", "gemma", "deepseek", "phi", "lfm", "gpt-oss"]
            )
            and ":" in model
        ):
            use_npu = True

        # thinking 모델 감지: -tk 접미사 OR tier-thinking 요청
        is_thinking_model = model and (
            "-tk" in model or original_tier == "tier-thinking"
        )

        # On-Demand: 필요한 서버가 준비될 때까지 대기
        if use_lemonade:
            if not await self._ensure_lemonade_model_ready():
                await self.publish_error(request_id, "Lemonade server model failed to load")
                return
        elif use_npu:
            if is_thinking_model:
                if not await self._ensure_provider_ready("flm-llm-thinking"):
                    await self.publish_error(
                        request_id, "FLM LLM Thinking server failed to start"
                    )
                    return
            else:
                if not await self._ensure_provider_ready("flm-llm"):
                    await self.publish_error(
                        request_id, "FLM LLM server failed to start"
                    )
                    return
        else:
            if not await self._ensure_provider_ready("llama-server"):
                await self.publish_error(request_id, "Llama server failed to start")
                return

        full_content = ""
        try:
            if use_lemonade:
                logger.info(
                    f"[{request_id}] Using lemonade-server (GPU, summarize, model={model})..."
                )
                url = f"{settings.lemonade_server_url}/api/v1/chat/completions"
                actual_model = model
                provider_name = "lemonade-server"
            elif use_npu:
                if is_thinking_model:
                    logger.info(
                        f"[{request_id}] Using FLM LLM Thinking server (NPU, model={model})..."
                    )
                    url = f"{settings.flm_llm_thinking_url}/v1/chat/completions"
                    actual_model = model
                    provider_name = "flm-llm-thinking"
                else:
                    logger.info(
                        f"[{request_id}] Using FLM LLM server (NPU, model={model})..."
                    )
                    url = f"{settings.flm_llm_url}/v1/chat/completions"
                    actual_model = model if model else "lfm2.5-it:1.2b"
                    provider_name = "flm-llm"
            else:
                logger.info(f"[{request_id}] Using llama-server (GPU)...")
                url = f"{settings.llama_server_url}/v1/chat/completions"
                actual_model = model
                provider_name = "llama-server"

            payload = {
                "model": actual_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,  # 스트리밍 활성화
            }

            # HTTP 요청 span
            completion_tokens = 0

            with trace_http_request(
                provider_name,
                url,
                model=actual_model,
                device="npu" if use_npu else "gpu",
            ) as span:
                # 스트리밍 요청 (Read Timeout 무제한)
                async with self.http_client.stream(
                    "POST", url, json=payload, timeout=None
                ) as response:
                    response.raise_for_status()

                    if span:
                        span.set_attribute("http.status_code", response.status_code)

                    # SSE 파싱 및 청크 발행
                    first_chunk_logged = False
                    async for line in response.aiter_lines():
                        if not line or not line.strip():
                            continue

                        # 디버그: 첫 번째 줄 로깅 (응답 형식 확인용)
                        if not first_chunk_logged:
                            logger.info(
                                f"[{request_id}] First raw line: {line[:200]}..."
                            )
                            first_chunk_logged = True

                        if not line.startswith("data: "):
                            # SSE 형식이 아닌 경우 로깅 (FLM이 다른 형식을 사용할 수 있음)
                            if completion_tokens < 3:
                                logger.warning(
                                    f"[{request_id}] Non-SSE line: {line[:100]}"
                                )
                            continue

                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            logger.info(f"[{request_id}] Received [DONE] signal")
                            break

                        try:
                            chunk_json = json.loads(data_str)
                            choices = chunk_json.get("choices", [])
                            if not choices:
                                logger.debug(
                                    f"[{request_id}] Chunk has no choices: {list(chunk_json.keys())}"
                                )
                                continue

                            delta = choices[0].get("delta") or {}
                            if not isinstance(delta, dict):
                                logger.warning(
                                    f"[{request_id}] Delta is not dict: {type(delta)} - {delta}"
                                )
                                delta = {}

                            # FLM 추론 모델: reasoning_content (추론 과정) + content (최종 답변)
                            reasoning_content = str(
                                delta.get("reasoning_content") or ""
                            )
                            content = str(delta.get("content") or "")

                            # 디버그: delta 내용 확인 (첫 몇 개만)
                            if completion_tokens < 3:
                                logger.debug(
                                    f"[{request_id}] Delta keys: {list(delta.keys())}, reasoning={bool(reasoning_content)}, content={bool(content)}"
                                )

                            # 추론 과정이 있으면 <think> 태그로 감싸서 전송
                            if reasoning_content:
                                # 추론 시작 시 <think> 태그 추가
                                if full_content is None:
                                    full_content = ""
                                if not full_content.startswith("<think>"):
                                    full_content = "<think>"
                                    await self.publish_chunk(request_id, "<think>")
                                full_content += reasoning_content
                                completion_tokens += 1
                                await self.publish_chunk(request_id, reasoning_content)

                            # 최종 답변이 있으면 </think> 닫고 전송
                            if content:
                                if full_content is None:
                                    full_content = ""
                                # 추론 과정이 있었다면 </think> 닫기
                                if (
                                    full_content.startswith("<think>")
                                    and "</think>" not in full_content
                                ):
                                    full_content += "</think>\n\n"
                                    await self.publish_chunk(request_id, "</think>\n\n")

                                # NPU (FLM) 서버가 Delta 대신 Full Text를 보내는 경우 처리 (안전하게)
                                try:
                                    clean_content = (
                                        full_content.replace("<think>", "")
                                        .replace("</think>", "")
                                        .strip()
                                    )
                                    if (
                                        use_npu
                                        and content
                                        and clean_content
                                        and content.startswith(clean_content)
                                    ):
                                        content = content[len(clean_content) :]
                                except Exception:
                                    pass

                                if content:
                                    full_content += content
                                    completion_tokens += 1
                                    await self.publish_chunk(request_id, content)

                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.warning(f"Error parsing chunk: {e}")

            logger.info(
                f"[{request_id}] LLM completion completed ({len(full_content)} chars, {completion_tokens} tokens)"
            )
            if not full_content:
                logger.warning(
                    f"[{request_id}] Empty response! No content was captured from streaming"
                )

            # 최종 결과 발행 (호환성 및 완료 신호)
            result = {
                "choices": [
                    {"message": {"content": full_content}, "finish_reason": "stop"}
                ],
                "usage": {"completion_tokens": completion_tokens},
            }

            # 응답 발행 span
            with trace_response_publish(request_id, success=True):
                await self.publish_response(request_id, result)

            # lemonade-server idle timeout 활동 기록
            # (외부 provider_name은 llama-server로 매핑되므로 직접 기록)
            if use_lemonade and self.idle_manager:
                self.idle_manager.record_activity("lemonade-server")

        except httpx.TimeoutException:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"LLM completion timeout")
        except httpx.HTTPStatusError as e:
            error_detail = ""
            if e.response.status_code == 400:
                # 400 에러 시 사용 가능한 모델 목록 조회 시도
                try:
                    # url: .../v1/chat/completions -> base: .../v1/models
                    base_url = url.split("/v1/")[0]
                    models_resp = await self.http_client.get(
                        f"{base_url}/v1/models", timeout=5.0
                    )
                    if models_resp.status_code == 200:
                        models_data = models_resp.json()
                        available_models = [
                            m.get("id") for m in models_data.get("data", [])
                        ]
                        error_detail = (
                            f" (Available models: {', '.join(available_models)})"
                        )
                        logger.error(
                            f"[{request_id}] Invalid model requested. Available: {available_models}"
                        )
                except Exception as debug_err:
                    logger.warning(f"Failed to debug models: {debug_err}")

            with trace_response_publish(request_id, success=False):
                await self.publish_error(
                    request_id,
                    f"LLM HTTP error: {e.response.status_code} - {e.response.text}{error_detail}",
                )
        except Exception as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"LLM completion failed: {str(e)}")
        finally:
            # lemonade 요청 실패 시 백그라운드에서 모델 상태 점검 + 복구
            if use_lemonade and not full_content:
                if self._lemonade_recovery_task is None or self._lemonade_recovery_task.done():
                    self._lemonade_recovery_task = asyncio.create_task(
                        self._background_lemonade_recovery()
                    )

    async def handle_ocr(self, request_id: str, data: dict):
        """OCR Vision 작업 처리."""
        image_content_b64 = data.get("image_content")
        model = data.get("model", "qwen3vl-4b")
        prompt = data.get("prompt", "Extract all text from this image.")
        accuracy_mode = data.get("accuracy_mode", "speed")
        file_id = data.get("file_id")  # Worker에서 전달한 file_id

        logger.info(
            f"[{request_id}] Starting OCR (model={model}, mode={accuracy_mode}, file_id={file_id})..."
        )

        use_npu = accuracy_mode == "speed"

        # On-Demand: 필요한 서버가 준비될 때까지 대기
        if use_npu:
            if not await self._ensure_provider_ready("flm-ocr"):
                await self.publish_error(request_id, "FLM OCR server failed to start")
                return
        else:
            if not await self._ensure_provider_ready("llama-ocr-server"):
                await self.publish_error(request_id, "Llama OCR server failed to start")
                return

        try:
            if use_npu:
                logger.info(f"[{request_id}] Using FLM OCR server (NPU)...")
                url = f"{settings.flm_ocr_url}/v1/chat/completions"
                ocr_model = "qwen3vl-it:4b"
                provider_name = "flm-ocr"
            else:
                logger.info(f"[{request_id}] Using llama-ocr-server (GPU)...")
                url = f"{settings.llama_ocr_server_url}/v1/chat/completions"
                ocr_model = model if model != "qwen3vl-4b" else "qwen3-vl"
                provider_name = "llama-ocr-server"

            # Provider가 실제 작업을 시작하기 직전에 이벤트 발행
            await self.publish_processing_started(request_id, "ocr", file_id)

            payload = {
                "model": ocr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_content_b64}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 8192,
                "temperature": 0.1,
            }

            # HTTP 요청 span
            with trace_http_request(
                provider_name, url, model=ocr_model, device="npu" if use_npu else "gpu"
            ) as span:
                response = await self.http_client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()

                if span:
                    span.set_attribute("http.status_code", response.status_code)

            ocr_text = ""
            if "choices" in result and len(result["choices"]) > 0:
                ocr_text = result["choices"][0].get("message", {}).get("content", "")

            logger.info(f"[{request_id}] OCR completed ({len(ocr_text)} chars)")
            result["text"] = ocr_text

            # 응답 발행 span
            with trace_response_publish(request_id, success=True) as span:
                if span:
                    span.set_attribute("ocr.text_length", len(ocr_text))
                await self.publish_response(request_id, result)

        except httpx.TimeoutException:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(request_id, f"OCR timeout")
        except httpx.HTTPStatusError as e:
            with trace_response_publish(request_id, success=False):
                await self.publish_error(
                    request_id, f"OCR HTTP error: {e.response.status_code}"
                )
        except Exception as e:
            logger.exception(f"[{request_id}] OCR failed")
            with trace_response_publish(request_id, success=False):
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
            "flm-llm-thinking": f"{settings.flm_llm_thinking_url}/v1/models",
            "flm-ocr": f"{settings.flm_ocr_url}/v1/models",
            "lemonade": f"{settings.lemonade_server_url}/api/v1/models",
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

    async def handle_embedding(self, request_id: str, data: dict):
        """임베딩 요청 처리 - FLM LLM 서버(/v1/embeddings OpenAI 형식) 직접 호출."""
        text = data.get("text", "")
        model = data.get("model", "embed-gemma:300m")

        logger.info(f"[{request_id}] Embedding request: model={model}, text_len={len(text)}")

        try:
            url = f"{settings.flm_llm_url}/v1/embeddings"
            response = await self.http_client.post(
                url,
                json={"input": text, "model": model},
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()
            await self.publish_response(request_id, result)
            logger.info(f"[{request_id}] Embedding completed")
        except httpx.TimeoutException:
            await self.publish_error(request_id, "Embedding timeout")
        except httpx.HTTPStatusError as e:
            await self.publish_error(request_id, f"Embedding HTTP error: {e.response.status_code}")
        except Exception as e:
            await self.publish_error(request_id, f"Embedding failed: {str(e)}")

    # ==========================================
    # Main Loop
    # ==========================================

    async def process_message(self, message_id: str, data: dict):
        """GPU 작업 메시지 처리 (JobTracker + OpenTelemetry 통합 + Device Group 동시성 제한)."""
        request_id = data.get("request_id", str(uuid.uuid4()))
        task_type = data.get("type")
        traceparent = data.get("traceparent")  # W3C Trace Context (Worker에서 주입)
        trace_id = data.get("trace_id")  # 레거시 호환성

        log_trace = f", traceparent={traceparent[:50]}..." if traceparent else ""
        logger.info(
            f"Processing message: id={message_id}, type={task_type}, request_id={request_id}{log_trace}"
        )

        # 프로바이더 결정
        provider = self._get_provider_for_task(task_type, data)

        # 프로바이더 이름 매핑 (provider -> provider_name)
        provider_name_map = {
            "whisper-cpp": "whisper-server",
            "insanely-fast": "insanely-fast-server",
            "diarization": "diarization-server",
            "llama-server": "llama-server",
            "llama-ocr-server": "llama-ocr-server",
            "flm-asr": "flm-asr",
            "flm-llm": "flm-llm",
            "flm-llm-thinking": "flm-llm-thinking",
            "flm-ocr": "flm-ocr",
        }
        provider_name = provider_name_map.get(provider, provider)

        # Device Group 확인 (동시성 제한용)
        device_group = self.PROVIDER_DEVICE_GROUP.get(provider_name)
        semaphore = self._device_semaphores.get(device_group) if device_group else None

        # health_check / embedding은 세마포어 없이 즉시 처리
        if task_type == "health_check":
            await self.handle_health_check(request_id, data)
            await self.redis.xack(
                settings.request_stream, settings.consumer_group, message_id
            )
            return

        if task_type == "embedding":
            await self.handle_embedding(request_id, data)
            await self.redis.xack(
                settings.request_stream, settings.consumer_group, message_id
            )
            return

        # 대기 중인 작업 수 추적 (로깅용)
        if device_group:
            self._pending_jobs[device_group] += 1
            pending = self._pending_jobs[device_group]
            if pending > 1:
                logger.info(
                    f"[Queue] {device_group.upper()} queue: {pending} jobs waiting (request_id={request_id})"
                )

        # Device Group 세마포어 획득 (동일 device group 내 순차 실행)
        async with semaphore if semaphore else asyncio.Lock():
            if device_group:
                self._pending_jobs[device_group] -= 1
                logger.info(
                    f"[Queue] {device_group.upper()} semaphore acquired for {task_type} (request_id={request_id})"
                )

            # ProviderManager에 작업 등록 (active_jobs 증가)
            await self.provider_manager.register_job(provider_name, message_id)

            # 작업 시작 기록
            job = None
            if self.job_tracker:
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

            # OpenTelemetry span 생성 (traceparent로 부모 trace에 연결)
            with trace_gpu_operation(
                operation_name=f"gpu.{task_type}" if task_type else "gpu.unknown",
                traceparent=traceparent,
                request_id=request_id,
                task_type=task_type,
                provider=provider,
                model=data.get("model"),
            ) as span:
                try:
                    if task_type == "diarization":
                        await self.handle_diarization(request_id, data)
                    elif task_type == "transcription":
                        await self.handle_transcription(request_id, data)
                    elif task_type == "llm_completion":
                        await self.handle_llm_completion(request_id, data)
                    elif task_type == "ocr":
                        await self.handle_ocr(request_id, data)
                    else:
                        await self.publish_error(
                            request_id, f"Unknown task type: {task_type}"
                        )
                        success = False
                        error_msg = f"Unknown task type: {task_type}"

                    await self.redis.xack(
                        settings.request_stream, settings.consumer_group, message_id
                    )

                except Exception as e:
                    logger.exception(f"Error processing message {message_id}: {e}")
                    await self.publish_error(request_id, str(e))
                    await self.redis.xack(
                        settings.request_stream, settings.consumer_group, message_id
                    )
                    success = False
                    error_msg = str(e)

                # span에 결과 기록
                record_operation_result(span, success=success, error=error_msg)

            # ProviderManager에서 작업 해제 (active_jobs 감소)
            await self.provider_manager.complete_job(provider_name, message_id)

            # Idle timeout 활동 기록 (On-Demand 프로바이더 자동 언로드용)
            if self.idle_manager:
                self.idle_manager.record_activity(provider_name)

            # 작업 완료 기록
            if job and self.job_tracker:
                await self.job_tracker.complete_job(
                    message_id, success=success, error=error_msg
                )

            if device_group:
                logger.info(
                    f"[Queue] {device_group.upper()} semaphore released for {task_type} (request_id={request_id})"
                )

    async def run(self):
        """메인 루프 - GPU 작업 + Control API 스트림 동시 처리."""
        await self.connect()

        logger.info("Provider Manager started. Waiting for messages...")
        logger.info(f"  GPU Stream: {settings.request_stream}")
        logger.info(f"  Control Stream: {settings.control_request_stream}")

        while self.is_running:
            try:
                # 양쪽 스트림 동시 읽기 (Multi-stream listening)
                streams_to_listen = {
                    settings.control_request_stream: ">",
                    settings.chat_request_stream: ">",
                    settings.media_request_stream: ">",
                    settings.recap_request_stream: ">",
                }

                # 호환성 유지: 기존 request_stream이 위 3개 중 하나와 다르면 추가
                if settings.request_stream not in streams_to_listen:
                    streams_to_listen[settings.request_stream] = ">"

                messages = await self.redis.xreadgroup(
                    settings.consumer_group,
                    settings.consumer_name,
                    streams_to_listen,
                    count=5,
                    block=5000,
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            # 스트림에 따라 처리 분기
                            if stream_name == settings.control_request_stream:
                                await self.process_control_message(
                                    message_id, message_data
                                )
                            else:
                                # GPU 작업 (media, chat, recap, legacy)은 모두 process_message로 처리
                                asyncio.create_task(
                                    self.process_message(message_id, message_data)
                                )

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
