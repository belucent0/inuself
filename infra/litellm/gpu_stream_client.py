"""GPU Stream Client - Redis Stream 기반 GPU 작업 요청/응답 처리.

Architecture V6.6: 메시징 기반 아키텍처
- LiteLLM (Docker) -> Redis Stream -> Provider Manager (Host) -> GPU Servers
- Docker → Host HTTP 통신 제거로 Docker Desktop 크래시 방지

V6.7: OpenTelemetry 분산 추적 지원
- Redis Stream 메시지에 traceparent 헤더 포함
- Worker → Provider Manager 간 trace context 전파
"""

import os
import json
import time
import uuid
import base64
import asyncio
from pathlib import Path
from typing import Optional, Any, AsyncIterator
import logging

logger = logging.getLogger(__name__)

# OpenTelemetry trace context 주입 (선택적)
try:
    from opentelemetry.propagate import inject as otel_inject
    from opentelemetry import trace as otel_trace

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    otel_inject = None
    otel_trace = None

import redis
import redis.asyncio as redis_async

# Stream Names (V8.1: Topic-based streams)
REQUEST_STREAM_LEGACY = "stream:gpu:requests"  # Deprecated
MEDIA_STREAM = "stream:media:requests"  # Audio, Vision
CHAT_STREAM = "stream:chat:requests"  # Simple, Thinking
RECAP_STREAM = "stream:recap:requests"  # Summary
RESPONSE_STREAM = "stream:gpu:responses"

# Timeouts
DEFAULT_TIMEOUT = 7200.0  # 2시간 (3시간 음성 파일의 화자분리 대응)


class GPUStreamClient:
    """Redis Stream 기반 GPU 작업 클라이언트 (동기)."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis: Optional[redis.Redis] = None

    @property
    def redis_client(self) -> redis.Redis:
        """Lazy Redis 연결."""
        if self._redis is None:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def _generate_request_id(self) -> str:
        """유니크 request_id 생성."""
        return f"{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"

    def _inject_trace_context(self, request_data: dict) -> dict:
        """현재 trace context를 request_data에 주입."""
        if OTEL_AVAILABLE and otel_inject:
            # traceparent 헤더 주입
            carrier = {}
            otel_inject(carrier)
            if "traceparent" in carrier:
                request_data["traceparent"] = carrier["traceparent"]
                logger.info(
                    f"[GPUStream] Trace context injected: traceparent={carrier['traceparent']}"
                )
            else:
                logger.warning("[GPUStream] No active trace context to inject")
        else:
            logger.warning(
                f"[GPUStream] OpenTelemetry not available (OTEL_AVAILABLE={OTEL_AVAILABLE})"
            )
        return request_data

    def _wait_for_response(self, request_id: str, timeout: float) -> dict:
        """Response Stream에서 결과 대기."""
        start_time = time.time()
        last_id = "0"

        while time.time() - start_time < timeout:
            try:
                # XREAD로 새 메시지 읽기 (1초 블로킹)
                messages = self.redis_client.xread(
                    {RESPONSE_STREAM: last_id},
                    count=10,
                    block=1000,
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            last_id = message_id

                            # 우리 request_id인지 확인
                            if message_data.get("request_id") == request_id:
                                if "error" in message_data:
                                    raise Exception(message_data["error"])
                                if "result" in message_data:
                                    return json.loads(message_data["result"])

            except redis.ConnectionError as e:
                logger.warning(f"Redis connection error, retrying: {e}")
                time.sleep(1)
                continue

        raise TimeoutError(
            f"No response received within {timeout}s for request_id={request_id}"
        )

    def request_diarization(
        self,
        audio_file_path: Path,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Diarization 요청.

        Args:
            audio_file_path: 오디오 파일 경로
            min_speakers: 최소 화자 수
            max_speakers: 최대 화자 수
            timeout: 타임아웃 (초)

        Returns:
            Diarization 결과 {model, segments, metrics}
        """
        request_id = self._generate_request_id()

        # 파일을 Base64로 인코딩
        with open(audio_file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode("utf-8")

        # 요청 데이터
        request_data = {
            "request_id": request_id,
            "type": "diarization",
            "file_path": str(audio_file_path),
            "file_content": file_content,
            "timestamp": str(time.time()),
        }
        if min_speakers is not None:
            request_data["min_speakers"] = str(min_speakers)
        if max_speakers is not None:
            request_data["max_speakers"] = str(max_speakers)

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(f"[GPUStream] Sending diarization request: request_id={request_id}")

        # Request Stream에 발행 (Media Stream)
        self.redis_client.xadd(MEDIA_STREAM, request_data)

        # 응답 대기
        result = self._wait_for_response(request_id, timeout)
        logger.info(f"[GPUStream] Diarization completed: request_id={request_id}")

        return result

    def request_transcription(
        self,
        audio_file_path: Path,
        model: str = "whisper-turbo",
        language: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """ASR Transcription 요청.

        Args:
            audio_file_path: 오디오 파일 경로
            model: 모델 이름
            language: 언어 코드
            timeout: 타임아웃 (초)

        Returns:
            Transcription 결과
        """
        request_id = self._generate_request_id()

        with open(audio_file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode("utf-8")

        request_data = {
            "request_id": request_id,
            "type": "transcription",
            "file_content": file_content,
            "model": model,
            "timestamp": str(time.time()),
        }
        if language:
            request_data["language"] = language

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending transcription request: request_id={request_id}, model={model}"
        )

        self.redis_client.xadd(MEDIA_STREAM, request_data)
        result = self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] Transcription completed: request_id={request_id}")
        return result

    def request_llm_completion(
        self,
        messages: list[dict],
        model: str = "tier-simple",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        target_server: str = "auto",
        timeout: float = 300.0,
    ) -> dict:
        """LLM Completion 요청.

        Args:
            messages: OpenAI 형식 메시지 리스트
            model: 모델 이름
            max_tokens: 최대 토큰 수
            temperature: 샘플링 온도
            target_server: 타겟 서버 ("auto", "flm", "llama")
            timeout: 타임아웃 (초)

        Returns:
            Completion 결과
        """
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "llm_completion",
            "messages": json.dumps(messages),
            "model": model,
            "max_tokens": str(max_tokens),
            "temperature": str(temperature),
            "target_server": target_server,
            "timestamp": str(time.time()),
        }

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending LLM completion request: request_id={request_id}, model={model}"
        )

        # Stream 분기 (Chat vs Recap)
        target_stream = RECAP_STREAM if model == "tier-recap" else CHAT_STREAM
        self.redis_client.xadd(target_stream, request_data)
        result = self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] LLM completion completed: request_id={request_id}")
        return result

    def request_ocr(
        self,
        image_base64: str,
        model: str = "qwen3vl-4b",
        prompt: str = "Extract all text from this image.",
        accuracy_mode: str = "speed",
        timeout: float = 300.0,
    ) -> dict:
        """OCR Vision 요청.

        Args:
            image_base64: Base64 인코딩된 이미지 데이터
            model: 모델 이름
            prompt: OCR 프롬프트
            accuracy_mode: 'speed' (FLM NPU) 또는 'accuracy' (GPU llama-ocr)
            timeout: 타임아웃 (초)

        Returns:
            OCR 결과 {text, model, ...}
        """
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "ocr",
            "image_content": image_base64,
            "model": model,
            "prompt": prompt,
            "accuracy_mode": accuracy_mode,
            "timestamp": str(time.time()),
        }

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending OCR request: request_id={request_id}, model={model}, mode={accuracy_mode}"
        )

        self.redis_client.xadd(MEDIA_STREAM, request_data)
        result = self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] OCR completed: request_id={request_id}")
        return result

    def check_health(self, service: str = "all", timeout: float = 30.0) -> dict:
        """GPU 서비스 Health Check.

        Args:
            service: 서비스 이름 ('all', 'diarization', 'whisper-cpp', 등)
            timeout: 타임아웃 (초)

        Returns:
            Health check 결과
        """
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "health_check",
            "service": service,
            "timestamp": str(time.time()),
        }

        self.redis_client.xadd(CHAT_STREAM, request_data)
        return self._wait_for_response(request_id, timeout)


# ==========================================
# Async Client (LiteLLM Custom Handler용)
# ==========================================


class AsyncGPUStreamClient:
    """Redis Stream 기반 GPU 작업 클라이언트 (비동기).

    LiteLLM Custom Handler에서 사용.
    """

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis: Optional[redis_async.Redis] = None

    async def get_redis(self) -> redis_async.Redis:
        """Lazy Redis 연결."""
        if self._redis is None:
            self._redis = redis_async.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def close(self):
        """연결 종료."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    def _generate_request_id(self) -> str:
        """유니크 request_id 생성."""
        return f"{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"

    def _inject_trace_context(self, request_data: dict) -> dict:
        """현재 trace context를 request_data에 주입."""
        if OTEL_AVAILABLE and otel_inject:
            carrier = {}
            otel_inject(carrier)
            if "traceparent" in carrier:
                request_data["traceparent"] = carrier["traceparent"]
                logger.info(
                    f"[GPUStream/Async] Trace context injected: traceparent={carrier['traceparent']}"
                )
            else:
                logger.warning("[GPUStream/Async] No active trace context to inject")
        else:
            logger.warning(
                f"[GPUStream/Async] OpenTelemetry not available (OTEL_AVAILABLE={OTEL_AVAILABLE})"
            )
        return request_data

    async def _wait_for_response(self, request_id: str, timeout: float) -> dict:
        """Response Stream에서 결과 대기 (비동기)."""
        redis_client = await self.get_redis()
        start_time = time.time()
        last_id = "0"

        while time.time() - start_time < timeout:
            try:
                # XREAD로 새 메시지 읽기 (1초 블로킹)
                messages = await redis_client.xread(
                    {RESPONSE_STREAM: last_id},
                    count=10,
                    block=1000,
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            last_id = message_id

                            # 우리 request_id인지 확인
                            if message_data.get("request_id") == request_id:
                                if "error" in message_data:
                                    raise Exception(message_data["error"])
                                if "result" in message_data:
                                    return json.loads(message_data["result"])

            except redis_async.ConnectionError as e:
                logger.warning(f"Redis connection error, retrying: {e}")
                await asyncio.sleep(1)
                continue

        raise TimeoutError(
            f"No response received within {timeout}s for request_id={request_id}"
        )

    async def _wait_for_stream_response(
        self, request_id: str, timeout: float
    ) -> AsyncIterator[dict]:
        """Response Stream에서 결과 스트리밍 대기 (비동기).

        Activity-based timeout: 청크가 들어올 때마다 타이머 리셋.
        추론 모드에서 <think> 과정이 오래 걸려도 타임아웃 방지.
        """
        redis_client = await self.get_redis()
        last_activity = time.time()  # 마지막 활동 시간 (activity-based timeout)
        last_id = "0"

        while time.time() - last_activity < timeout:
            try:
                # XREAD로 새 메시지 읽기 (1초 블로킹)
                messages = await redis_client.xread(
                    {RESPONSE_STREAM: last_id},
                    count=50,
                    block=1000,
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            last_id = message_id

                            # 우리 request_id인지 확인
                            if message_data.get("request_id") == request_id:
                                last_activity = time.time()  # 활동 타이머 리셋

                                if "error" in message_data:
                                    raise Exception(message_data["error"])

                                if "chunk" in message_data:
                                    yield {"chunk": message_data["chunk"]}

                                if "result" in message_data:
                                    yield {"result": json.loads(message_data["result"])}
                                    return

                                if "finish_reason" in message_data:
                                    return

            except redis_async.ConnectionError as e:
                logger.warning(f"Redis connection error, retrying: {e}")
                await asyncio.sleep(1)
                continue

    async def request_llm_completion_stream(
        self,
        messages: list[dict],
        model: str = "tier-simple",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        target_server: str = "auto",
        timeout: float = 300.0,
    ) -> AsyncIterator[dict]:
        """LLM Completion 요청 (스트리밍)."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "llm_completion",
            "messages": json.dumps(messages),
            "model": model,
            "max_tokens": str(max_tokens),
            "temperature": str(temperature),
            "target_server": target_server,
            "timestamp": str(time.time()),
        }

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending LLM completion stream request: request_id={request_id}, model={model}"
        )

        # Stream 분기 (Chat vs Recap)
        target_stream = RECAP_STREAM if model == "tier-recap" else CHAT_STREAM
        await redis_client.xadd(target_stream, request_data)

        async for chunk in self._wait_for_stream_response(request_id, timeout):
            yield chunk

        logger.info(
            f"[GPUStream] LLM stream completion completed: request_id={request_id}"
        )

    async def request_diarization(
        self,
        audio_file_path: Path,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Diarization 요청 (비동기)."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        # 파일을 Base64로 인코딩
        with open(audio_file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode("utf-8")

        request_data = {
            "request_id": request_id,
            "type": "diarization",
            "file_path": str(audio_file_path),
            "file_content": file_content,
            "timestamp": str(time.time()),
        }
        if min_speakers is not None:
            request_data["min_speakers"] = str(min_speakers)
        if max_speakers is not None:
            request_data["max_speakers"] = str(max_speakers)

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(f"[GPUStream] Sending diarization request: request_id={request_id}")

        await redis_client.xadd(MEDIA_STREAM, request_data)
        result = await self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] Diarization completed: request_id={request_id}")
        return result

    async def request_transcription(
        self,
        audio_file_path: Path,
        model: str = "whisper-turbo",
        language: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """ASR Transcription 요청 (비동기)."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        with open(audio_file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode("utf-8")

        request_data = {
            "request_id": request_id,
            "type": "transcription",
            "file_content": file_content,
            "model": model,
            "timestamp": str(time.time()),
        }
        if language:
            request_data["language"] = language

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending transcription request: request_id={request_id}, model={model}"
        )

        await redis_client.xadd(MEDIA_STREAM, request_data)
        result = await self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] Transcription completed: request_id={request_id}")
        return result

    async def request_llm_completion(
        self,
        messages: list[dict],
        model: str = "tier-simple",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        target_server: str = "auto",
        timeout: float = 300.0,
    ) -> dict:
        """LLM Completion 요청 (비동기)."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "llm_completion",
            "messages": json.dumps(messages),
            "model": model,
            "max_tokens": str(max_tokens),
            "temperature": str(temperature),
            "target_server": target_server,
            "timestamp": str(time.time()),
        }

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending LLM completion request: request_id={request_id}, model={model}"
        )

        # Stream 분기 (Chat vs Recap)
        target_stream = RECAP_STREAM if model == "tier-recap" else CHAT_STREAM
        await redis_client.xadd(target_stream, request_data)
        result = await self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] LLM completion completed: request_id={request_id}")
        return result

    async def request_ocr(
        self,
        image_data: bytes,
        model: str = "qwen3vl-4b",
        prompt: str = "Extract all text from this image.",
        accuracy_mode: str = "speed",
        timeout: float = 300.0,
    ) -> dict:
        """OCR Vision 요청 (비동기).

        Args:
            image_data: 이미지 바이너리 데이터
            model: 모델 이름
            prompt: OCR 프롬프트
            accuracy_mode: 'speed' (FLM NPU) 또는 'accuracy' (GPU llama-ocr)
            timeout: 타임아웃 (초)

        Returns:
            OCR 결과 {text, model, ...}
        """
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        image_b64 = base64.b64encode(image_data).decode("utf-8")

        request_data = {
            "request_id": request_id,
            "type": "ocr",
            "image_content": image_b64,
            "model": model,
            "prompt": prompt,
            "accuracy_mode": accuracy_mode,
            "timestamp": str(time.time()),
        }

        # Trace context 주입 (분산 추적)
        self._inject_trace_context(request_data)

        logger.info(
            f"[GPUStream] Sending OCR request: request_id={request_id}, model={model}, mode={accuracy_mode}"
        )

        await redis_client.xadd(MEDIA_STREAM, request_data)
        result = await self._wait_for_response(request_id, timeout)

        logger.info(f"[GPUStream] OCR completed: request_id={request_id}")
        return result

    async def check_health(self, service: str = "all", timeout: float = 30.0) -> dict:
        """GPU 서비스 Health Check (비동기)."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "health_check",
            "service": service,
            "timestamp": str(time.time()),
        }

        await redis_client.xadd(CHAT_STREAM, request_data)
        return await self._wait_for_response(request_id, timeout)


# ==========================================
# Singleton Instances
# ==========================================

# Sync client
_client_instance: Optional[GPUStreamClient] = None


def get_gpu_stream_client() -> GPUStreamClient:
    """GPUStreamClient 싱글톤 인스턴스 반환."""
    global _client_instance
    if _client_instance is None:
        _client_instance = GPUStreamClient()
    return _client_instance


# Async client
_async_client_instance: Optional[AsyncGPUStreamClient] = None


def get_async_gpu_stream_client() -> AsyncGPUStreamClient:
    """AsyncGPUStreamClient 싱글톤 인스턴스 반환."""
    global _async_client_instance
    if _async_client_instance is None:
        _async_client_instance = AsyncGPUStreamClient()
    return _async_client_instance
