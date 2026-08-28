"""OpenTelemetry 분산 추적 설정 (AI Gateway용).

GPU/NPU 프로바이더 호출 추적 및 병목 분석을 위한 모듈.

사용법:
    from middleware.telemetry import setup_telemetry, trace_provider_call

    # 초기화 (main.py에서)
    setup_telemetry()

    # 프로바이더 호출 추적
    with trace_provider_call("gpu", "whisper-large-v3", file_id=123) as span:
        response = await client.transcription(...)
        span.set_attribute("output.length", len(response.text))
"""
import os
import time
import functools
from contextlib import contextmanager
from typing import Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap, inject, extract

import logging

logger = logging.getLogger(__name__)


# ============================================
# 트레이싱 필터 설정 (노이즈 제거)
# ============================================

EXCLUDED_PATHS = frozenset({"/health", "/ready", "/metrics", "/healthz", "/liveliness"})
EXCLUDED_REDIS_COMMANDS = frozenset({
    "PING", "INFO", "CONFIG", "CLIENT", "CLUSTER",
    "XREAD", "XREADGROUP",
})


class FilteringSpanProcessor(SpanProcessor):
    """헬스체크 및 노이즈 span 필터링."""
    
    def __init__(self, next_processor: SpanProcessor):
        self._next = next_processor
    
    def on_start(self, span, parent_context=None):
        self._next.on_start(span, parent_context)
    
    def on_end(self, span):
        if span.status.status_code == StatusCode.ERROR:
            self._next.on_end(span)
            return
        
        # span 이름에서도 경로 체크 (ex: "GET /health/liveliness http send")
        span_name = span.name or ""
        http_target = span.attributes.get("http.target", "")
        http_url = span.attributes.get("http.url", "")
        
        # 헬스체크 필터링 - span 이름, target, url 모두 체크
        for path in EXCLUDED_PATHS:
            if path in span_name or path in http_target or path in http_url:
                return  # span 드롭
        
        # Redis 필터링
        db_statement = span.attributes.get("db.statement", "")
        if db_statement:
            cmd = db_statement.split()[0].upper() if db_statement.split() else ""
            if cmd in EXCLUDED_REDIS_COMMANDS:
                return
        
        self._next.on_end(span)
    
    def shutdown(self):
        self._next.shutdown()
    
    def force_flush(self, timeout_millis=30000):
        return self._next.force_flush(timeout_millis)


# 전역 설정
_initialized = False
_tracer_provider: Optional[TracerProvider] = None


def setup_telemetry(service_name: str = None, app=None) -> None:
    """AI Gateway용 OpenTelemetry 초기화."""
    global _initialized, _tracer_provider

    if _initialized:
        return

    service = service_name or os.getenv("OTEL_SERVICE_NAME", "asr-ai-gateway")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.warning("[Telemetry] OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled")
        _initialized = True
        return

    try:
        resource = Resource.create({
            SERVICE_NAME: service,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })

        _tracer_provider = TracerProvider(resource=resource)

        # HTTP exporter는 /v1/traces 경로 필요
        traces_endpoint = f"{endpoint.rstrip('/')}/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=traces_endpoint,
        )

        # FilteringSpanProcessor로 노이즈 제거
        batch_processor = BatchSpanProcessor(exporter)
        filtering_processor = FilteringSpanProcessor(batch_processor)
        _tracer_provider.add_span_processor(filtering_processor)
        
        logger.info("[Telemetry] Noise filtering enabled")

        trace.set_tracer_provider(_tracer_provider)
        set_global_textmap(TraceContextTextMapPropagator())

        # HTTPX 자동 계측
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except Exception:
            pass

        # FastAPI 자동 계측 (수신 요청 추적)
        if app:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer_provider)
                logger.info("[Telemetry] FastAPI instrumented")
            except Exception as e:
                logger.warning(f"[Telemetry] Failed to instrument FastAPI: {e}")

        # Redis 자동 계측
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor
            RedisInstrumentor().instrument()
        except Exception:
            pass

        _initialized = True
        logger.info(f"[Telemetry] LiteLLM initialized: service={service}, endpoint={endpoint}")

    except Exception as e:
        logger.error(f"[Telemetry] Failed to initialize: {e}")
        _initialized = True


def get_tracer(name: str = "litellm-handler") -> trace.Tracer:
    """Tracer 인스턴스 반환."""
    return trace.get_tracer(name)


def get_current_span() -> Optional[Span]:
    """현재 활성 span 반환."""
    return trace.get_current_span()


def get_trace_id() -> Optional[str]:
    """현재 trace_id 반환."""
    span = get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, '032x')
    return None


# ============================================
# 병목 분석용 속성 키
# ============================================

class ProviderAttributes:
    """GPU/NPU 프로바이더 병목 분석용 속성 키."""

    # 프로바이더 정보
    PROVIDER_TYPE = "provider.type"  # gpu, npu
    PROVIDER_NAME = "provider.name"  # ai-llm, ai-asr-vllm, ai-diarize, ai-embedding
    PROVIDER_URL = "provider.url"
    PROVIDER_HEALTHY = "provider.healthy"

    # 모델 정보
    MODEL_NAME = "model.name"
    MODEL_SIZE = "model.size"

    # 요청 정보
    FILE_ID = "file.id"
    REQUEST_TYPE = "request.type"  # completion, transcription, chat
    REQUEST_MODE = "request.mode"  # speed, accuracy

    # 성능 지표
    QUEUE_WAIT_MS = "queue.wait_ms"
    PROCESSING_MS = "processing_ms"
    GPU_MEMORY_USED = "gpu.memory_used_mb"
    NPU_MEMORY_USED = "npu.memory_used_mb"

    # Redis Stream (V6.6)
    STREAM_REQUEST_ID = "stream.request_id"
    STREAM_WAIT_MS = "stream.wait_ms"


@contextmanager
def trace_provider_call(
    provider_type: str,
    model: str,
    request_type: str = "completion",
    file_id: int = None,
    provider_name: str = None,
    **extra_attributes
):
    """GPU/NPU 프로바이더 호출 추적.

    Args:
        provider_type: "gpu" 또는 "npu"
        model: 모델 이름
        request_type: 요청 유형 (completion, transcription, chat, ocr)
        file_id: 파일 ID (있는 경우)
        provider_name: 프로바이더 이름 (llama, whisper-cpp, flm 등)
        **extra_attributes: 추가 속성

    사용 예:
        with trace_provider_call("gpu", "whisper-large-v3", "transcription", file_id=123) as span:
            response = await transcribe(...)
            span.set_attribute("output.length", len(response.text))
    """
    tracer = get_tracer("provider")
    span_name = f"provider.{provider_type}.{request_type}"

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute(ProviderAttributes.PROVIDER_TYPE, provider_type)
        span.set_attribute(ProviderAttributes.MODEL_NAME, model)
        span.set_attribute(ProviderAttributes.REQUEST_TYPE, request_type)

        if file_id is not None:
            span.set_attribute(ProviderAttributes.FILE_ID, file_id)
        if provider_name:
            span.set_attribute(ProviderAttributes.PROVIDER_NAME, provider_name)

        start_time = time.time()

        for key, value in extra_attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
        finally:
            elapsed_ms = (time.time() - start_time) * 1000
            span.set_attribute(ProviderAttributes.PROCESSING_MS, elapsed_ms)


@contextmanager
def trace_redis_stream_call(
    operation: str,
    request_id: str = None,
    **extra_attributes
):
    """Redis Stream 작업 추적 (V6.6).

    Args:
        operation: 작업 유형 (send_request, wait_response)
        request_id: Stream 요청 ID
        **extra_attributes: 추가 속성
    """
    tracer = get_tracer("redis-stream")
    span_name = f"stream.{operation}"

    with tracer.start_as_current_span(span_name) as span:
        if request_id:
            span.set_attribute(ProviderAttributes.STREAM_REQUEST_ID, request_id)

        start_time = time.time()

        for key, value in extra_attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
        finally:
            elapsed_ms = (time.time() - start_time) * 1000
            span.set_attribute(ProviderAttributes.STREAM_WAIT_MS, elapsed_ms)


@contextmanager
def trace_routing_decision(
    model: str,
    selected_provider: str = None,
    gpu_utilization: float = None,
    npu_health: bool = None,
):
    """라우팅 결정 추적.

    어떤 프로바이더가 선택되었는지와 그 이유를 추적합니다.
    """
    tracer = get_tracer("routing")

    with tracer.start_as_current_span("routing.decision") as span:
        span.set_attribute(ProviderAttributes.MODEL_NAME, model)

        if selected_provider:
            span.set_attribute("routing.selected_provider", selected_provider)
        if gpu_utilization is not None:
            span.set_attribute("routing.gpu_utilization", gpu_utilization)
        if npu_health is not None:
            span.set_attribute("routing.npu_health", npu_health)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def inject_trace_context(carrier: dict) -> dict:
    """현재 trace context를 carrier에 주입."""
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict) -> trace.Context:
    """carrier에서 trace context 추출."""
    return extract(carrier)
