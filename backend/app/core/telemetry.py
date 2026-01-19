"""OpenTelemetry 분산 추적 설정.

확장성 있는 설계로 병목 분석을 위한 커스텀 속성을 지원합니다.

사용법:
    from app.core.telemetry import setup_telemetry, get_tracer, trace_operation

    # FastAPI 앱에서 초기화
    setup_telemetry(app)

    # 커스텀 span 생성
    tracer = get_tracer("my-service")
    with tracer.start_as_current_span("operation") as span:
        span.set_attribute("file_id", 123)
        ...

    # 또는 데코레이터 사용
    @trace_operation("process_file", attributes={"type": "asr"})
    def process_file(file_id: int):
        ...
"""
import os
import functools
from contextlib import contextmanager
from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap, inject, extract

from .logging import logger


# 전역 설정
_initialized = False
_tracer_provider: Optional[TracerProvider] = None
_propagator = TraceContextTextMapPropagator()


def setup_telemetry(app=None, service_name: str = None) -> None:
    """OpenTelemetry 초기화.

    Args:
        app: FastAPI 앱 (자동 계측용)
        service_name: 서비스 이름 (환경변수 OTEL_SERVICE_NAME으로 오버라이드 가능)
    """
    global _initialized, _tracer_provider

    if _initialized:
        logger.debug("[Telemetry] Already initialized, skipping")
        return

    # 서비스 이름 결정
    service = service_name or os.getenv("OTEL_SERVICE_NAME", "asr-unknown")

    # OTLP 엔드포인트 확인
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.warning("[Telemetry] OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled")
        _initialized = True
        return

    try:
        # Resource 생성 (서비스 메타데이터)
        resource = Resource.create({
            SERVICE_NAME: service,
            "service.version": os.getenv("APP_VERSION", "1.0.0"),
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })

        # TracerProvider 설정
        _tracer_provider = TracerProvider(resource=resource)

        # OTLP Exporter 설정 (gRPC)
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=True,  # Docker 내부 통신이므로 TLS 불필요
        )

        # BatchSpanProcessor로 성능 최적화
        processor = BatchSpanProcessor(exporter)
        _tracer_provider.add_span_processor(processor)

        # 전역 TracerProvider 설정
        trace.set_tracer_provider(_tracer_provider)

        # W3C Trace Context 전파 설정
        set_global_textmap(_propagator)

        # FastAPI 자동 계측 (선택적)
        if app:
            _instrument_fastapi(app)

        # 공통 라이브러리 자동 계측
        _instrument_common_libraries()

        _initialized = True
        logger.info(f"[Telemetry] Initialized: service={service}, endpoint={endpoint}")

    except Exception as e:
        logger.error(f"[Telemetry] Failed to initialize: {e}")
        _initialized = True  # 재시도 방지


def _instrument_fastapi(app) -> None:
    """FastAPI 자동 계측."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        logger.info("[Telemetry] FastAPI instrumented")
    except Exception as e:
        logger.warning(f"[Telemetry] Failed to instrument FastAPI: {e}")


def _instrument_common_libraries() -> None:
    """공통 라이브러리 자동 계측."""
    # Redis
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
        logger.debug("[Telemetry] Redis instrumented")
    except Exception as e:
        logger.debug(f"[Telemetry] Redis instrumentation skipped: {e}")

    # HTTPX
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.debug("[Telemetry] HTTPX instrumented")
    except Exception as e:
        logger.debug(f"[Telemetry] HTTPX instrumentation skipped: {e}")

    # SQLAlchemy
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument()
        logger.debug("[Telemetry] SQLAlchemy instrumented")
    except Exception as e:
        logger.debug(f"[Telemetry] SQLAlchemy instrumentation skipped: {e}")

    # Celery (Producer side)
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
        logger.debug("[Telemetry] Celery instrumented (Producer)")
    except Exception as e:
        logger.debug(f"[Telemetry] Celery instrumentation skipped: {e}")


def get_tracer(name: str = "asr-app") -> trace.Tracer:
    """Tracer 인스턴스 반환.

    Args:
        name: Tracer 이름 (모듈/서비스 식별용)

    Returns:
        OpenTelemetry Tracer
    """
    return trace.get_tracer(name)


def get_current_span() -> Optional[Span]:
    """현재 활성 span 반환."""
    return trace.get_current_span()


def get_trace_id() -> Optional[str]:
    """현재 trace_id 반환 (hex string)."""
    span = get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, '032x')
    return None


def get_span_id() -> Optional[str]:
    """현재 span_id 반환 (hex string)."""
    span = get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().span_id, '016x')
    return None


# ============================================
# 병목 분석용 확장 기능
# ============================================

class BottleneckAttributes:
    """병목 분석을 위한 표준 속성 키."""

    # 파일/콘텐츠 식별
    FILE_ID = "file.id"
    CONTENT_TYPE = "content.type"  # audio, video, image, document
    ORIGINAL_FILENAME = "file.name"
    FILE_SIZE_BYTES = "file.size_bytes"

    # 처리 단계
    PIPELINE_STAGE = "pipeline.stage"  # upload, preprocessing, asr, diarization, llm, ocr
    PROCESSING_MODE = "processing.mode"  # speed, accuracy, case1-4

    # 리소스 사용
    PROVIDER_TYPE = "provider.type"  # gpu, npu, cpu
    MODEL_NAME = "model.name"
    QUEUE_NAME = "queue.name"
    QUEUE_WAIT_MS = "queue.wait_ms"

    # 성능 지표
    CHUNK_INDEX = "chunk.index"
    CHUNK_TOTAL = "chunk.total"
    INPUT_DURATION_SEC = "input.duration_sec"  # 오디오/비디오 길이
    OUTPUT_LENGTH = "output.length"  # 결과 텍스트 길이


def set_bottleneck_attributes(
    span: Span,
    file_id: int = None,
    content_type: str = None,
    pipeline_stage: str = None,
    provider_type: str = None,
    model_name: str = None,
    queue_name: str = None,
    **extra_attributes
) -> None:
    """병목 분석용 속성을 span에 설정.

    Args:
        span: 대상 span
        file_id: 파일 ID
        content_type: 콘텐츠 유형 (audio, video, image, document)
        pipeline_stage: 처리 단계 (upload, asr, llm, ocr 등)
        provider_type: 프로바이더 유형 (gpu, npu)
        model_name: 사용된 모델 이름
        queue_name: 큐 이름
        **extra_attributes: 추가 속성
    """
    if not span or not span.is_recording():
        return

    if file_id is not None:
        span.set_attribute(BottleneckAttributes.FILE_ID, file_id)
    if content_type:
        span.set_attribute(BottleneckAttributes.CONTENT_TYPE, content_type)
    if pipeline_stage:
        span.set_attribute(BottleneckAttributes.PIPELINE_STAGE, pipeline_stage)
    if provider_type:
        span.set_attribute(BottleneckAttributes.PROVIDER_TYPE, provider_type)
    if model_name:
        span.set_attribute(BottleneckAttributes.MODEL_NAME, model_name)
    if queue_name:
        span.set_attribute(BottleneckAttributes.QUEUE_NAME, queue_name)

    for key, value in extra_attributes.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def trace_pipeline_stage(
    stage_name: str,
    file_id: int = None,
    **attributes
):
    """파이프라인 단계 추적용 컨텍스트 매니저.

    사용 예:
        with trace_pipeline_stage("asr_processing", file_id=123, model="whisper"):
            result = process_audio(...)
    """
    tracer = get_tracer("pipeline")
    with tracer.start_as_current_span(stage_name) as span:
        set_bottleneck_attributes(
            span,
            file_id=file_id,
            pipeline_stage=stage_name,
            **attributes
        )
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def trace_operation(
    operation_name: str = None,
    attributes: dict = None
) -> Callable:
    """함수/메서드 추적용 데코레이터.

    사용 예:
        @trace_operation("process_file", attributes={"type": "asr"})
        def process_file(file_id: int):
            ...
    """
    def decorator(func: Callable) -> Callable:
        name = operation_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(name) as span:
                # 기본 속성 설정
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                # file_id가 인자에 있으면 자동 추가
                if 'file_id' in kwargs:
                    span.set_attribute(BottleneckAttributes.FILE_ID, kwargs['file_id'])

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                if 'file_id' in kwargs:
                    span.set_attribute(BottleneckAttributes.FILE_ID, kwargs['file_id'])

                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


# ============================================
# Trace Context 전파 (서비스 간 연결)
# ============================================

def inject_trace_context(carrier: dict) -> dict:
    """현재 trace context를 carrier에 주입.

    Celery task나 HTTP 요청에 trace context를 전달할 때 사용.

    Args:
        carrier: trace context를 담을 dict (headers 등)

    Returns:
        trace context가 주입된 carrier

    사용 예:
        headers = {}
        inject_trace_context(headers)
        celery_task.apply_async(kwargs={...}, headers=headers)
    """
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict) -> trace.Context:
    """carrier에서 trace context 추출.

    Celery task나 HTTP 요청에서 trace context를 복원할 때 사용.

    Args:
        carrier: trace context가 담긴 dict

    Returns:
        복원된 trace context

    사용 예:
        context = extract_trace_context(task.request.headers or {})
        with tracer.start_as_current_span("task", context=context):
            ...
    """
    return extract(carrier)


def create_linked_span(
    name: str,
    parent_context: trace.Context = None,
    **attributes
) -> trace.Span:
    """부모 context와 연결된 새 span 생성.

    서비스 간 trace 연결에 사용.
    """
    tracer = get_tracer("linked")
    return tracer.start_span(name, context=parent_context, attributes=attributes)
