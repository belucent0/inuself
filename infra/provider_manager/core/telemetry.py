"""OpenTelemetry 분산 추적 설정 (Provider Manager용).

Architecture V6.7: Worker → Provider Manager 분산 추적 연결
- Redis Stream 메시지에서 traceparent 추출
- GPU/NPU 작업을 부모 trace에 연결
"""
import os
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("ProviderManager.Telemetry")

# OpenTelemetry 설정 변수
_initialized = False
_tracer_provider = None

# OpenTelemetry imports (선택적)
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.trace import Status, StatusCode, Span, SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.propagate import set_global_textmap, extract
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logger.warning("[Telemetry] OpenTelemetry not available. Install: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http")


# ============================================
# 트레이싱 필터 설정 (노이즈 제거)
# ============================================

EXCLUDED_PATHS = frozenset({"/health", "/ready", "/metrics", "/healthz", "/liveliness"})
EXCLUDED_REDIS_COMMANDS = frozenset({
    "PING", "INFO", "CONFIG", "CLIENT", "CLUSTER",
    "XREAD", "XREADGROUP",
})


class FilteringSpanProcessor:
    """헬스체크 및 노이즈 span 필터링 (OTEL 없어도 안전)."""
    
    def __init__(self, next_processor):
        self._next = next_processor
    
    def on_start(self, span, parent_context=None):
        self._next.on_start(span, parent_context)
    
    def on_end(self, span):
        if OTEL_AVAILABLE and span.status.status_code == StatusCode.ERROR:
            self._next.on_end(span)
            return
        
        # span 이름에서도 경로 체크
        span_name = getattr(span, 'name', '') or ""
        http_target = span.attributes.get("http.target", "") if hasattr(span, 'attributes') else ""
        http_url = span.attributes.get("http.url", "") if hasattr(span, 'attributes') else ""
        
        for path in EXCLUDED_PATHS:
            if path in span_name or path in http_target or path in http_url:
                return
        
        db_statement = span.attributes.get("db.statement", "") if hasattr(span, 'attributes') else ""
        if db_statement:
            cmd = db_statement.split()[0].upper() if db_statement.split() else ""
            if cmd in EXCLUDED_REDIS_COMMANDS:
                return
        
        self._next.on_end(span)
    
    def shutdown(self):
        self._next.shutdown()
    
    def force_flush(self, timeout_millis=30000):
        return self._next.force_flush(timeout_millis)


def setup_telemetry(service_name: str = None) -> None:
    """Provider Manager용 OpenTelemetry 초기화.

    Args:
        service_name: 서비스 이름 (환경변수 OTEL_SERVICE_NAME으로 오버라이드 가능)
    """
    global _initialized, _tracer_provider

    if _initialized:
        logger.debug("[Telemetry] Already initialized, skipping")
        return

    if not OTEL_AVAILABLE:
        logger.warning("[Telemetry] OpenTelemetry not available, tracing disabled")
        _initialized = True
        return

    service = service_name or os.getenv("OTEL_SERVICE_NAME", "provider-manager")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.warning("[Telemetry] OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled")
        _initialized = True
        return

    try:
        # Resource 생성 (서비스 메타데이터)
        resource = Resource.create({
            SERVICE_NAME: service,
            "service.version": os.getenv("APP_VERSION", "7.4.0"),
            "deployment.environment": os.getenv("ENVIRONMENT", "production"),
        })

        # TracerProvider 설정
        _tracer_provider = TracerProvider(resource=resource)

        # OTLP Exporter 설정 (HTTP - /v1/traces 경로 필요)
        traces_endpoint = f"{endpoint.rstrip('/')}/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=traces_endpoint,
        )

        # FilteringSpanProcessor로 노이즈 제거
        batch_processor = BatchSpanProcessor(exporter)
        filtering_processor = FilteringSpanProcessor(batch_processor)
        _tracer_provider.add_span_processor(filtering_processor)
        
        logger.info("[Telemetry] Noise filtering enabled")

        # 전역 TracerProvider 설정
        trace.set_tracer_provider(_tracer_provider)

        # W3C Trace Context 전파 설정
        set_global_textmap(TraceContextTextMapPropagator())

        _initialized = True
        logger.info(f"[Telemetry] Initialized: service={service}, endpoint={endpoint}")

    except Exception as e:
        logger.error(f"[Telemetry] Failed to initialize: {e}")
        _initialized = True


def get_tracer(name: str = "provider-manager") -> "trace.Tracer":
    """Tracer 인스턴스 반환."""
    if not OTEL_AVAILABLE:
        return None
    return trace.get_tracer(name)


def get_trace_id() -> str:
    """현재 활성 trace의 trace_id를 반환. 없으면 빈 문자열 반환."""
    if not OTEL_AVAILABLE:
        return ""

    try:
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().trace_id, '032x')
    except Exception:
        pass

    return ""


def extract_trace_context(traceparent: str) -> Optional["trace.Context"]:
    """traceparent 문자열에서 trace context 추출.

    Args:
        traceparent: W3C Trace Context traceparent 헤더 값

    Returns:
        추출된 trace context 또는 None
    """
    if not OTEL_AVAILABLE or not traceparent:
        logger.info(f"[Telemetry] No traceparent to extract (OTEL={OTEL_AVAILABLE}, traceparent={traceparent is not None})")
        return None

    try:
        carrier = {"traceparent": traceparent}
        context = extract(carrier)

        # 추출된 context에서 span context 확인
        span_context = trace.get_current_span(context).get_span_context()
        if span_context.is_valid:
            trace_id = format(span_context.trace_id, '032x')
            span_id = format(span_context.span_id, '016x')
            logger.info(f"[Telemetry] Extracted parent context: trace_id={trace_id}, span_id={span_id}")
        else:
            logger.warning(f"[Telemetry] Extracted context is invalid for traceparent={traceparent}")

        return context
    except Exception as e:
        logger.warning(f"[Telemetry] Failed to extract trace context: {e}")
        return None


@contextmanager
def trace_gpu_operation(
    operation_name: str,
    traceparent: str = None,
    request_id: str = None,
    task_type: str = None,
    provider: str = None,
    **extra_attributes
):
    """GPU/NPU 작업 추적용 컨텍스트 매니저 (Service Graph 가시성을 위한 SERVER span).

    Args:
        operation_name: span 이름 (예: "diarization", "transcription")
        traceparent: 부모 trace context (Redis Stream 메시지에서 추출)
        request_id: 요청 ID
        task_type: 작업 유형 (diarization, transcription, llm_completion, ocr)
        provider: 사용된 프로바이더 이름
        **extra_attributes: 추가 속성
    """
    if not OTEL_AVAILABLE:
        # OpenTelemetry 없으면 그냥 실행
        yield None
        return

    tracer = get_tracer("gpu-operation")
    if not tracer:
        yield None
        return

    # 부모 context 추출
    context = extract_trace_context(traceparent) if traceparent else None

    # SpanKind.SERVER로 Service Graph에 표시 (LiteLLM/Worker → Provider Manager 연결)
    with tracer.start_as_current_span(
        operation_name,
        context=context,
        kind=SpanKind.SERVER,
    ) as span:
        # Service Graph 연결을 위한 peer.service 설정
        span.set_attribute("peer.service", "asr-ai-gateway")  # 요청 발신자
        span.set_attribute("messaging.system", "redis-stream")
        span.set_attribute("messaging.operation", "receive")

        # 기본 속성 설정
        if request_id:
            span.set_attribute("request.id", request_id)
        if task_type:
            span.set_attribute("task.type", task_type)
        if provider:
            span.set_attribute("provider.name", provider)

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


def record_operation_result(span, success: bool, error: str = None, **metrics):
    """작업 결과를 span에 기록.

    Args:
        span: OpenTelemetry span
        success: 성공 여부
        error: 에러 메시지 (실패 시)
        **metrics: 추가 메트릭 (duration_ms, output_length 등)
    """
    if not OTEL_AVAILABLE or not span:
        return

    span.set_attribute("operation.success", success)

    if error:
        span.set_attribute("error.message", error)
        span.set_status(Status(StatusCode.ERROR, error))
    else:
        span.set_status(Status(StatusCode.OK))

    for key, value in metrics.items():
        if value is not None:
            span.set_attribute(f"metrics.{key}", value)


# ==========================================
# Child Span Helpers (세분화된 추적)
# ==========================================

@contextmanager
def trace_provider_load(provider_name: str, on_demand: bool = True):
    """프로바이더 로딩 추적 (On-Demand).

    Args:
        provider_name: 프로바이더 이름
        on_demand: On-Demand 로딩 여부
    """
    if not OTEL_AVAILABLE:
        yield None
        return

    tracer = get_tracer("provider-load")
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span("provider.load") as span:
        span.set_attribute("provider.name", provider_name)
        span.set_attribute("provider.on_demand", on_demand)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


@contextmanager
def trace_http_request(
    provider_name: str,
    url: str,
    method: str = "POST",
    **extra_attributes
):
    """HTTP 요청 추적 (Service Graph 가시성을 위한 CLIENT span).

    Args:
        provider_name: 프로바이더 이름
        url: 요청 URL
        method: HTTP 메서드
        **extra_attributes: 추가 속성
    """
    if not OTEL_AVAILABLE:
        yield None
        return

    tracer = get_tracer("http-request")
    if not tracer:
        yield None
        return

    # SpanKind.CLIENT로 Service Graph에 표시
    with tracer.start_as_current_span(
        f"http.{provider_name}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("http.method", method)
        span.set_attribute("http.url", url)
        span.set_attribute("provider.name", provider_name)

        # Service Graph 연결을 위한 peer.service 설정
        span.set_attribute("peer.service", provider_name)

        for key, value in extra_attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


@contextmanager
def trace_response_publish(request_id: str, success: bool = True):
    """응답 발행 추적.

    Args:
        request_id: 요청 ID
        success: 성공 여부 (에러 응답인지)
    """
    if not OTEL_AVAILABLE:
        yield None
        return

    tracer = get_tracer("response-publish")
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span("response.publish") as span:
        span.set_attribute("request.id", request_id)
        span.set_attribute("response.success", success)

        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
