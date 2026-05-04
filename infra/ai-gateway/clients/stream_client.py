"""Redis Stream client — Provider Manager 경유 임베딩 호출 전용.

refactor/inference 이후 chat / OCR / ASR / Diarize는 ai-gateway가 추론
컨테이너를 직접 httpx로 호출하므로 Provider Manager(Redis Stream) 경유는
임베딩(`request_embedding`) 한 경로만 남았다.

남은 책임:
- AsyncGPUStreamClient.request_embedding: routes/embeddings.py 가 사용
- get_async_gpu_stream_client(): 싱글톤 factory (main.py가 lifespan 종료 시 close)

OpenTelemetry trace context는 Worker → Provider Manager 간 전파를 위해 보존.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# OpenTelemetry trace context 주입 (선택)
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.propagate import inject as otel_inject
    from opentelemetry.trace import SpanKind

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    otel_inject = None
    otel_trace = None
    SpanKind = None

import redis.asyncio as redis_async

CHAT_STREAM = "stream:chat:requests"
RESPONSE_STREAM = "stream:gpu:responses"


class AsyncGPUStreamClient:
    """Redis Stream 기반 임베딩 클라이언트 (비동기)."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis: Optional[redis_async.Redis] = None

    async def get_redis(self) -> redis_async.Redis:
        if self._redis is None:
            self._redis = redis_async.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def close(self):
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    def _generate_request_id(self) -> str:
        return f"{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"

    def _inject_trace_context(self, request_data: dict) -> dict:
        if OTEL_AVAILABLE and otel_inject:
            carrier: dict = {}
            otel_inject(carrier)
            if "traceparent" in carrier:
                request_data["traceparent"] = carrier["traceparent"]
        return request_data

    def _create_client_span(self, task_type: str, stream_name: str):
        if not OTEL_AVAILABLE or not otel_trace:
            from contextlib import nullcontext
            return nullcontext()

        tracer = otel_trace.get_tracer("gpu-stream-client-async")
        return tracer.start_as_current_span(
            f"redis.stream.{task_type}",
            kind=SpanKind.CLIENT,
            attributes={
                "peer.service": "provider-manager",
                "messaging.system": "redis-stream",
                "messaging.destination": stream_name,
                "messaging.operation": "send",
            },
        )

    async def _wait_for_response(self, request_id: str, timeout: float) -> dict:
        redis_client = await self.get_redis()
        start_time = time.time()
        last_id = "0"

        while time.time() - start_time < timeout:
            try:
                messages = await redis_client.xread(
                    {RESPONSE_STREAM: last_id},
                    count=10,
                    block=1000,
                )
                if messages:
                    for _stream_name, stream_messages in messages:
                        for message_id, message_data in stream_messages:
                            last_id = message_id
                            if message_data.get("request_id") != request_id:
                                continue
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

    async def request_embedding(
        self,
        text: str,
        model: str = "embeddinggemma:300m",
        timeout: float = 15.0,
    ) -> dict:
        """임베딩 벡터 요청. Provider Manager → embedding-server 경유."""
        redis_client = await self.get_redis()
        request_id = self._generate_request_id()

        request_data = {
            "request_id": request_id,
            "type": "embedding",
            "text": text,
            "model": model,
            "timestamp": str(time.time()),
        }

        with self._create_client_span("embedding", CHAT_STREAM):
            self._inject_trace_context(request_data)
            logger.info(f"[GPUStream] Sending embedding request: request_id={request_id}")
            await redis_client.xadd(CHAT_STREAM, request_data)

        result = await self._wait_for_response(request_id, timeout)
        logger.info(f"[GPUStream] Embedding completed: request_id={request_id}")
        return result


_async_client_instance: Optional[AsyncGPUStreamClient] = None


def get_async_gpu_stream_client() -> AsyncGPUStreamClient:
    """AsyncGPUStreamClient 싱글톤 인스턴스 반환."""
    global _async_client_instance
    if _async_client_instance is None:
        _async_client_instance = AsyncGPUStreamClient()
    return _async_client_instance
