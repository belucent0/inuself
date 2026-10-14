"""Gateway tracing with explicit metadata; request content is never collected."""

import logging
import os
from contextvars import ContextVar

from opentelemetry.context import Context, attach, detach
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


logger = logging.getLogger(__name__)
_tracer_provider = None
_tracer = None
_request_span = ContextVar("gateway_request_span", default=None)
_ROUTES = {"/v1/chat/completions": "chat", "/v1/embeddings": "embedding"}
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"}
_ERROR_TYPES = {"invalid_request", "forbidden", "unavailable", "overloaded", "upstream_error", "timed_out"}


def setup_telemetry():
    global _tracer_provider, _tracer
    if _tracer_provider is not None:
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("Gateway tracing disabled")
        return
    provider = None
    try:
        resource = Resource({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "asr-ai-gateway"),
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })
        provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _tracer = provider.get_tracer("ai-gateway")
        _tracer_provider = provider
        logger.info("Gateway tracing enabled")
    except Exception as exc:
        _tracer_provider = _tracer = None
        if provider:
            try:
                provider.shutdown()
            except Exception as shutdown_error:
                logger.warning("Gateway tracing shutdown failed exception=%s", type(shutdown_error).__name__)
        logger.warning("Gateway tracing setup failed exception=%s", type(exc).__name__)


def shutdown_telemetry():
    global _tracer_provider, _tracer
    provider, _tracer_provider, _tracer = _tracer_provider, None, None
    if provider:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("Gateway tracing shutdown failed exception=%s", type(exc).__name__)


def annotate_routing(*, workload=None, execution_scope=None, provider=None, model=None, reason=None):
    span = _request_span.get()
    if span is not None:
        for key, value in (
            ("gateway.workload", workload), ("gateway.execution_scope", execution_scope),
            ("gateway.provider", provider), ("gateway.model", model), ("gateway.routing_reason", reason),
        ):
            if value is not None:
                span.set_attribute(key, value)


def annotate_error(error_type, status=None):
    span = _request_span.get()
    if span is not None and isinstance(error_type, str) and error_type in _ERROR_TYPES:
        span.set_attribute("gateway.error_type", error_type)
        if status is None or status >= 500:
            span.set_status(Status(StatusCode.ERROR))


class GatewayTracingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        tracer = _tracer
        path = scope.get("path")
        if tracer is None or scope["type"] != "http" or path not in _ROUTES:
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        method = method if method in _METHODS else "OTHER"
        parents = []
        for name, value in scope.get("headers", []):
            if name.lower() == b"traceparent":
                try:
                    parents.append(value.decode("ascii"))
                except UnicodeDecodeError:
                    parents.append("")
        carrier = {"traceparent": parents[0]} if len(parents) == 1 else {}
        context = TraceContextTextMapPropagator().extract(carrier, context=Context())
        context_token = attach(context)
        try:
            with tracer.start_as_current_span(
                f"{method} {path}", context=context, kind=SpanKind.SERVER,
                record_exception=False, set_status_on_exception=False,
                attributes={"http.request.method": method, "http.route": path, "gateway.workload": _ROUTES[path]},
            ) as span:
                token = _request_span.set(span)

                async def traced_send(message):
                    if message["type"] == "http.response.start":
                        status = message["status"]
                        span.set_attribute("http.response.status_code", status)
                        if status >= 500:
                            span.set_status(Status(StatusCode.ERROR))
                    await send(message)

                try:
                    await self.app(scope, receive, traced_send)
                except BaseException as exc:
                    span.set_attribute("error.type", type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
                finally:
                    _request_span.reset(token)
        finally:
            detach(context_token)
