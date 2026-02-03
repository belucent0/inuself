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
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap, inject, extract

from .logging import logger


# ============================================
# OpenLLMetry (LLM Observability)
# ============================================

def _init_openllmetry(service_name: str) -> bool:
    """OpenLLMetry 초기화 (LLM 호출 자동 계측).

    traceloop-sdk가 OpenAI, LangChain, LangGraph 등을 자동으로 계측합니다.
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
        # TRACELOOP_BASE_URL을 설정하면 Traceloop 클라우드 대신 지정된 엔드포인트 사용
        os.environ.setdefault("TRACELOOP_BASE_URL", endpoint)

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

# 헬스체크 경로 (추적 제외)
EXCLUDED_PATHS = frozenset({"/health", "/ready", "/metrics", "/healthz", "/favicon.ico", "/liveliness"})

# Redis 노이즈 명령어 (추적 제외) - XREAD/XREADGROUP 폴링 포함
EXCLUDED_REDIS_COMMANDS = frozenset({
    "PING", "INFO", "CONFIG", "CLIENT", "CLUSTER",
    "XREAD", "XREADGROUP",  # Redis Stream 폴링
})


class FilteringSpanProcessor(SpanProcessor):
    """헬스체크 및 노이즈 span 필터링 프로세서.
    
    - /health, /metrics 등 헬스체크 요청 제외
    - Redis PING, XREAD 등 노이즈 명령어 제외
    - 에러 발생 시 항상 수집 (필터 우회)
    """
    
    def __init__(self, next_processor: SpanProcessor):
        self._next = next_processor
    
    def on_start(self, span, parent_context=None):
        self._next.on_start(span, parent_context)
    
    def on_end(self, span):
        # 에러 span은 항상 수집
        if span.status.status_code == StatusCode.ERROR:
            self._next.on_end(span)
            return
        
        # span 이름에서도 경로 체크 (ex: "GET /health http send")
        span_name = span.name or ""
        http_target = span.attributes.get("http.target", "")
        http_url = span.attributes.get("http.url", "")
        http_route = span.attributes.get("http.route", "")
        
        # 헬스체크 필터링 - span 이름, target, url, route 모두 체크
        for path in EXCLUDED_PATHS:
            if path in span_name or path in http_target or path in http_url or path in http_route:
                return  # span 드롭
        
        # Redis 명령어 필터링
        db_statement = span.attributes.get("db.statement", "")
        if db_statement:
            cmd = db_statement.split()[0].upper() if db_statement.split() else ""
            if cmd in EXCLUDED_REDIS_COMMANDS:
                return  # span 드롭
        
        self._next.on_end(span)
    
    def shutdown(self):
        self._next.shutdown()
    
    def force_flush(self, timeout_millis=30000):
        return self._next.force_flush(timeout_millis)


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

        # OTLP Exporter 설정 (HTTP)
        # Jaeger HTTP endpoint는 /v1/traces 경로 필요
        http_endpoint = f"{endpoint}/v1/traces" if not endpoint.endswith("/v1/traces") else endpoint
        exporter = OTLPSpanExporter(
            endpoint=http_endpoint,
        )

        # BatchSpanProcessor를 FilteringSpanProcessor로 감싸서 노이즈 제거
        batch_processor = BatchSpanProcessor(exporter)
        filtering_processor = FilteringSpanProcessor(batch_processor)
        _tracer_provider.add_span_processor(filtering_processor)
        
        logger.info("[Telemetry] Noise filtering enabled: health checks, Redis PING/INFO excluded")

        # 전역 TracerProvider 설정
        trace.set_tracer_provider(_tracer_provider)

        # W3C Trace Context 전파 설정
        set_global_textmap(_propagator)

        # FastAPI 자동 계측 (선택적)
        if app:
            _instrument_fastapi(app)

        # 공통 라이브러리 자동 계측
        _instrument_common_libraries()

        # run_in_executor 자동 컨텍스트 전파 패치
        patch_run_in_executor()

        # OpenLLMetry 초기화 (LLM 호출 자동 계측)
        _init_openllmetry(service)

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

    # Celery (Producer side) - Service Graph 연결을 위해 peer.service 속성 추가
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        def celery_producer_hook(span, task):
            """Celery Producer span에 Service Graph용 속성 추가."""
            span.set_attribute("peer.service", "asr-worker")
            span.set_attribute("messaging.system", "celery")
            span.set_attribute("messaging.destination", task.name if task else "unknown")

        CeleryInstrumentor().instrument(
            request_hook=celery_producer_hook,
        )
        logger.debug("[Telemetry] Celery instrumented (Producer) with peer.service")
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


# ============================================
# run_in_executor용 Context 전파 유틸리티
# ============================================

def preserve_otel_context(func: Callable) -> Callable:
    """run_in_executor에서 OpenTelemetry context를 유지하는 래퍼 생성.

    asyncio.run_in_executor()는 별도 스레드에서 실행되어 OTEL context가 손실됨.
    이 함수로 래핑하면 context가 스레드로 전파됨.

    사용 예:
        from app.core.telemetry import preserve_otel_context

        # Before (context 손실)
        job_id = await loop.run_in_executor(None, enqueue_func)

        # After (context 전파)
        job_id = await loop.run_in_executor(None, preserve_otel_context(enqueue_func))

    Args:
        func: 실행할 callable

    Returns:
        OTEL context가 전파되는 래핑된 callable
    """
    from opentelemetry import context as otel_context

    current_ctx = otel_context.get_current()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        token = otel_context.attach(current_ctx)
        try:
            return func(*args, **kwargs)
        finally:
            otel_context.detach(token)

    return wrapper


async def run_in_executor_with_otel(
    loop,
    executor,
    func: Callable,
    *args,
    **kwargs
):
    """OpenTelemetry context를 유지하며 executor에서 함수 실행.

    asyncio.run_in_executor()의 OTEL-aware 대체 함수.

    사용 예:
        from app.core.telemetry import run_in_executor_with_otel

        # Before
        result = await loop.run_in_executor(None, func, arg1, arg2)

        # After
        result = await run_in_executor_with_otel(loop, None, func, arg1, arg2)

    Args:
        loop: asyncio event loop
        executor: executor (None for default)
        func: 실행할 callable
        *args, **kwargs: func에 전달할 인자

    Returns:
        func의 반환값
    """
    from functools import partial

    if args or kwargs:
        func = partial(func, *args, **kwargs)

    return await loop.run_in_executor(executor, preserve_otel_context(func))


# ============================================
# 전역 패치: run_in_executor 자동 컨텍스트 전파
# ============================================

_original_run_in_executor = None


def patch_run_in_executor():
    """asyncio.AbstractEventLoop.run_in_executor를 패치하여 OTEL 컨텍스트 자동 전파.

    이 함수를 호출하면 모든 run_in_executor 호출에서 OTEL 컨텍스트가 자동으로
    스레드로 전파됩니다. 더 이상 preserve_otel_context()를 수동으로 래핑할 필요가 없습니다.

    사용법:
        # main.py 또는 앱 초기화 시
        from app.core.telemetry import patch_run_in_executor
        patch_run_in_executor()

        # 이후 모든 run_in_executor 호출은 자동으로 OTEL 컨텍스트 전파
        await loop.run_in_executor(None, func)  # 자동으로 컨텍스트 전파됨

    Note:
        - 이 패치는 idempotent합니다 (여러 번 호출해도 안전)
        - 앱 시작 시 한 번만 호출하면 됩니다
    """
    global _original_run_in_executor

    import asyncio
    from opentelemetry import context as otel_context

    # 이미 패치되었으면 스킵
    if _original_run_in_executor is not None:
        logger.debug("[OTEL] run_in_executor already patched, skipping")
        return

    # 원본 저장
    _original_run_in_executor = asyncio.AbstractEventLoop.run_in_executor

    def patched_run_in_executor(self, executor, func, *args):
        """OTEL 컨텍스트를 자동으로 전파하는 run_in_executor."""
        current_ctx = otel_context.get_current()

        @functools.wraps(func)
        def wrapper():
            token = otel_context.attach(current_ctx)
            try:
                return func(*args) if args else func()
            finally:
                otel_context.detach(token)

        return _original_run_in_executor(self, executor, wrapper)

    # 패치 적용
    asyncio.AbstractEventLoop.run_in_executor = patched_run_in_executor
    logger.info("[OTEL] Patched run_in_executor for automatic context propagation")
