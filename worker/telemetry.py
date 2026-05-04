"""OpenTelemetry 분산 추적 설정 (Worker용).

Celery 태스크 추적 및 AI Gateway/GPU 호출 추적을 위한 모듈.

사용법:
    # celery_app.py에서 초기화
    from worker.telemetry import setup_worker_telemetry
    setup_worker_telemetry()

    # 태스크에서 span 추가
    from worker.telemetry import trace_celery_task, set_task_attributes

    @celery_app.task(bind=True)
    def my_task(self, file_id: int):
        with trace_celery_task(self, file_id=file_id) as span:
            span.set_attribute("custom", "value")
            ...
"""

import os
import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode, Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap, inject, extract

from .logging_config import logger


# ============================================
# OpenLLMetry (LLM Observability)
# ============================================


def _init_openllmetry(service_name: str) -> bool:
    """OpenLLMetry 초기화 (LLM 호출 자동 계측).

    traceloop-sdk가 OpenAI, LangChain 등을 자동으로 계측합니다.
    기존 TracerProvider를 사용하므로 LLM trace도 Tempo로 전송됩니다.

    Returns:
        초기화 성공 여부
    """
    try:
        from traceloop.sdk import Traceloop

        # OTLP endpoint (기존 Tempo)
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            logger.debug("[OpenLLMetry] No OTLP endpoint, skipping")
            return False

        # Traceloop이 기존 OTEL exporter를 사용하도록 환경변수 설정
        os.environ.setdefault("TRACELOOP_BASE_URL", endpoint)
        # Tempo는 OTLP metrics endpoint를 제공하지 않으므로 Traceloop metrics 비활성화
        os.environ.setdefault("TRACELOOP_METRICS_ENABLED", "false")

        # 기존 TracerProvider 사용 (Tempo로 전송)
        Traceloop.init(
            app_name=service_name,
            disable_batch=True,  # 기존 OTEL exporter 사용
        )
        logger.info("[OpenLLMetry] Initialized: LLM calls will be traced")
        return True
    except ImportError:
        logger.debug("[OpenLLMetry] traceloop-sdk not installed, skipping")
        return False
    except Exception as e:
        logger.warning(f"[OpenLLMetry] Failed to initialize: {e}")
        return False


# ============================================
# 트레이싱 필터 설정 (노이즈 제거)
# ============================================

EXCLUDED_PATHS = frozenset({"/health", "/ready", "/metrics", "/healthz", "/liveliness"})
EXCLUDED_REDIS_COMMANDS = frozenset(
    {
        "PING",
        "INFO",
        "CONFIG",
        "CLIENT",
        "CLUSTER",
        "XREAD",
        "XREADGROUP",
    }
)


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

        # span 이름에서도 경로 체크
        span_name = span.name or ""
        http_target = span.attributes.get("http.target", "")
        http_url = span.attributes.get("http.url", "")

        for path in EXCLUDED_PATHS:
            if path in span_name or path in http_target or path in http_url:
                return

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
_propagator = TraceContextTextMapPropagator()


def setup_worker_telemetry(service_name: str = None) -> None:
    """Worker용 OpenTelemetry 초기화.

    Args:
        service_name: 서비스 이름 (환경변수로 오버라이드 가능)
    """
    global _initialized, _tracer_provider

    if _initialized:
        logger.debug("[Telemetry] Already initialized, skipping")
        return

    service = service_name or os.getenv("OTEL_SERVICE_NAME", "asr-worker")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.warning(
            "[Telemetry] OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled"
        )
        _initialized = True
        return

    try:
        # Resource 생성
        resource = Resource.create(
            {
                SERVICE_NAME: service,
                "service.version": os.getenv("APP_VERSION", "1.0.0"),
                "deployment.environment": os.getenv("ENVIRONMENT", "development"),
            }
        )

        # TracerProvider 설정
        _tracer_provider = TracerProvider(resource=resource)

        # OTLP Exporter (HTTP)
        # Jaeger HTTP endpoint는 /v1/traces 경로 필요
        http_endpoint = (
            f"{endpoint}/v1/traces" if not endpoint.endswith("/v1/traces") else endpoint
        )
        exporter = OTLPSpanExporter(
            endpoint=http_endpoint,
        )

        # FilteringSpanProcessor로 노이즈 제거
        batch_processor = BatchSpanProcessor(exporter)
        filtering_processor = FilteringSpanProcessor(batch_processor)
        _tracer_provider.add_span_processor(filtering_processor)

        logger.info("[Telemetry] Noise filtering enabled")

        trace.set_tracer_provider(_tracer_provider)
        set_global_textmap(_propagator)

        # Celery 자동 계측
        _instrument_celery()

        # 공통 라이브러리 계측
        _instrument_common_libraries()

        # OpenLLMetry 초기화 (LLM 호출 자동 계측)
        _init_openllmetry(service)

        _initialized = True
        logger.info(
            f"[Telemetry] Worker initialized: service={service}, endpoint={endpoint}"
        )

    except Exception as e:
        logger.error(f"[Telemetry] Failed to initialize: {e}")
        _initialized = True


def _instrument_celery() -> None:
    """Celery 자동 계측 - Service Graph 연결을 위해 peer.service 속성 추가."""
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        def celery_consumer_hook(span, task):
            """Celery Consumer span에 Service Graph용 속성 추가."""
            span.set_attribute("peer.service", "asr-backend")
            span.set_attribute("messaging.system", "celery")
            span.set_attribute(
                "messaging.destination", task.name if task else "unknown"
            )

        CeleryInstrumentor().instrument(
            request_hook=celery_consumer_hook,
        )
        logger.info("[Telemetry] Celery instrumented with peer.service")
    except Exception as e:
        logger.warning(f"[Telemetry] Failed to instrument Celery: {e}")


def _instrument_common_libraries() -> None:
    """공통 라이브러리 계측."""
    # Redis
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
        logger.debug("[Telemetry] Redis instrumented")
    except Exception:
        pass

    # HTTPX
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.debug("[Telemetry] HTTPX instrumented")
    except Exception:
        pass


def get_tracer(name: str = "asr-worker") -> trace.Tracer:
    """Tracer 인스턴스 반환."""
    return trace.get_tracer(name)


def get_current_span() -> Optional[Span]:
    """현재 활성 span 반환."""
    return trace.get_current_span()


def get_trace_id() -> Optional[str]:
    """현재 trace_id 반환 (hex string)."""
    span = get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, "032x")
    return None


# ============================================
# 병목 분석용 속성 키 (Backend와 동일)
# ============================================


class BottleneckAttributes:
    """병목 분석을 위한 표준 속성 키."""

    FILE_ID = "file.id"
    CONTENT_TYPE = "content.type"
    ORIGINAL_FILENAME = "file.name"
    FILE_SIZE_BYTES = "file.size_bytes"

    PIPELINE_STAGE = "pipeline.stage"
    PROCESSING_MODE = "processing.mode"

    PROVIDER_TYPE = "provider.type"
    MODEL_NAME = "model.name"
    QUEUE_NAME = "queue.name"
    QUEUE_WAIT_MS = "queue.wait_ms"

    CHUNK_INDEX = "chunk.index"
    CHUNK_TOTAL = "chunk.total"
    INPUT_DURATION_SEC = "input.duration_sec"
    OUTPUT_LENGTH = "output.length"

    # Worker 전용
    CELERY_TASK_ID = "celery.task_id"
    CELERY_RETRY_COUNT = "celery.retry_count"


# ============================================
# Celery Task 추적
# ============================================


@contextmanager
def trace_celery_task(
    task_instance, file_id: int = None, pipeline_stage: str = None, **extra_attributes
):
    """Celery 태스크 내에서 상세 추적을 위한 컨텍스트 매니저.

    Backend에서 주입한 trace context (traceparent)를 추출하여
    부모 span과 연결합니다.

    사용 예:
        @celery_app.task(bind=True)
        def process_asr_task(self, file_id: int, ...):
            with trace_celery_task(self, file_id=file_id, pipeline_stage="asr") as span:
                # 처리 로직
                span.set_attribute("model", "whisper-large-v3")
                ...
    """
    tracer = get_tracer("celery-task")
    task_name = task_instance.name if hasattr(task_instance, "name") else "unknown"
    span_name = f"task.{task_name.split('.')[-1]}"

    # Backend에서 주입한 trace context 추출 (분산 추적 연결)
    parent_context = None
    raw_headers = None
    if hasattr(task_instance, "request"):
        raw_headers = task_instance.request.headers
        logger.info(
            f"[Telemetry] Celery request.headers type={type(raw_headers)}, value={raw_headers}"
        )
        if raw_headers:
            headers = dict(raw_headers)
            logger.info(f"[Telemetry] Celery headers dict: {headers}")
            parent_context = extract_trace_context(headers)
            logger.info(f"[Telemetry] Extracted parent context: {parent_context}")

    # Service Graph용 SERVER span 생성 (Backend → Worker 연결 표시)
    with tracer.start_as_current_span(
        span_name,
        context=parent_context,
        kind=SpanKind.SERVER,  # CLIENT → SERVER 쌍으로 Service Graph 연결
    ) as span:
        # Service Graph 연결 속성
        span.set_attribute("peer.service", "asr-backend")
        span.set_attribute("messaging.system", "celery")
        span.set_attribute("messaging.operation", "receive")

        # 디버그: span이 실제로 시작되었는지 확인
        current_span = trace.get_current_span()
        if current_span and current_span.get_span_context().is_valid:
            ctx = current_span.get_span_context()
            logger.info(
                f"[Telemetry] Span started: trace_id={format(ctx.trace_id, '032x')}, span_id={format(ctx.span_id, '016x')}"
            )
        else:
            logger.warning(
                f"[Telemetry] Span NOT started or invalid! current_span={current_span}"
            )

        # Celery 메타데이터
        if hasattr(task_instance, "request"):
            span.set_attribute(
                BottleneckAttributes.CELERY_TASK_ID, task_instance.request.id or ""
            )
            span.set_attribute(
                BottleneckAttributes.CELERY_RETRY_COUNT,
                task_instance.request.retries or 0,
            )

        # 파이프라인 속성
        if file_id is not None:
            span.set_attribute(BottleneckAttributes.FILE_ID, str(file_id))
        if pipeline_stage:
            span.set_attribute(BottleneckAttributes.PIPELINE_STAGE, pipeline_stage)

        # 추가 속성
        for key, value in extra_attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


# ============================================
# AI Gateway/GPU 호출 추적
# ============================================


@contextmanager
def trace_llm_call(
    model: str, provider_type: str = "gpu", file_id: int = None, **extra_attributes
):
    """AI Gateway API 호출 추적.

    사용 예:
        with trace_llm_call("whisper-large-v3", provider_type="gpu", file_id=123) as span:
            response = ai_gateway_client.transcription(...)
            span.set_attribute("output.length", len(response.text))
    """
    tracer = get_tracer("llm-client")
    span_name = f"llm.{model}"

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute(BottleneckAttributes.MODEL_NAME, model)
        span.set_attribute(BottleneckAttributes.PROVIDER_TYPE, provider_type)

        if file_id is not None:
            span.set_attribute(BottleneckAttributes.FILE_ID, file_id)

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
            span.set_attribute("duration_ms", elapsed_ms)


@contextmanager
def trace_pipeline_operation(
    operation_name: str,
    file_id: int = None,
    chunk_index: int = None,
    chunk_total: int = None,
    **extra_attributes,
):
    """파이프라인 내 개별 작업 추적.

    청크 단위 처리, 병합 등의 세부 작업 추적에 사용.

    사용 예:
        with trace_pipeline_operation("chunk_transcription", file_id=123, chunk_index=1, chunk_total=5):
            result = transcribe_chunk(...)
    """
    tracer = get_tracer("pipeline")

    with tracer.start_as_current_span(operation_name) as span:
        if file_id is not None:
            span.set_attribute(BottleneckAttributes.FILE_ID, file_id)
        if chunk_index is not None:
            span.set_attribute(BottleneckAttributes.CHUNK_INDEX, chunk_index)
        if chunk_total is not None:
            span.set_attribute(BottleneckAttributes.CHUNK_TOTAL, chunk_total)

        for key, value in extra_attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


# ============================================
# Trace Context 전파
# ============================================


def inject_trace_context(carrier: dict) -> dict:
    """현재 trace context를 carrier에 주입."""
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict) -> trace.Context:
    """carrier에서 trace context 추출."""
    return extract(carrier)


def trace_operation(operation_name: str = None, **default_attributes) -> Callable:
    """함수 추적용 데코레이터.

    사용 예:
        @trace_operation("process_audio")
        def process_audio(file_id: int, ...):
            ...
    """

    def decorator(func: Callable) -> Callable:
        name = operation_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(name) as span:
                for key, value in default_attributes.items():
                    span.set_attribute(key, value)

                if "file_id" in kwargs:
                    span.set_attribute(BottleneckAttributes.FILE_ID, kwargs["file_id"])

                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(name) as span:
                for key, value in default_attributes.items():
                    span.set_attribute(key, value)

                if "file_id" in kwargs:
                    span.set_attribute(BottleneckAttributes.FILE_ID, kwargs["file_id"])

                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator
