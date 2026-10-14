import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from opentelemetry import baggage, context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, TraceFlags, TraceState, StatusCode

import main
from middleware import telemetry
from services.provider_pool import ProviderPool
from services.routing import ProviderResult
from test_chat_routing import FakeChunk, FakeClient, FakeResponse, FakeStream
from test_provider_pool import _policy
from utils import auth


TRACE_ID = "1234567890abcdef1234567890abcdef"
PARENT_ID = "1234567890abcdef"
HEADERS = {
    "Authorization": "Bearer SECRET-gateway-key",
    "traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01",
    "tracestate": "private=SECRET-tracestate",
    "baggage": "private=SECRET-baggage",
    "Cookie": "private=SECRET-cookie",
    "x-private": "SECRET-custom-header",
}


class TelemetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource({"service.name": "asr-ai-gateway", "service.version": "1.0.0", "deployment.environment": "test"}), shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer_patch = patch.object(telemetry, "_tracer", self.provider.get_tracer("ai-gateway"))
        self.tracer_patch.start()
        self.auth_patch = patch.object(auth, "GATEWAY_API_KEY", "SECRET-gateway-key")
        self.auth_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "policy.json"
        path.write_text(json.dumps(_policy()), encoding="utf-8")
        self.pool = ProviderPool(path)
        self.pool.states["npu-chat"].health = "healthy"
        self.pool.states["gpu-llm"].health = "healthy"
        main.app.state.provider_pool = self.pool
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://gateway")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.pool.close()
        self.provider.shutdown()
        self.temp.cleanup()
        self.tracer_patch.stop()
        self.auth_patch.stop()

    def spans(self):
        return self.exporter.get_finished_spans()

    def assert_safe(self, spans=None):
        spans = self.spans() if spans is None else spans
        serialized = "\n".join(span.to_json() for span in spans)
        self.assertNotIn("SECRET", serialized)
        for span in spans:
            self.assertEqual(span.events, ())
            self.assertIsNone(span.status.description)
            self.assertEqual(list(span.context.trace_state.items()), [])

    async def test_real_asgi_span_keeps_parent_and_selected_configured_metadata_without_private_values(self):
        from routes import chat

        with (
            patch.object(chat, "DEPLOY_MODE", "local-gpu"),
            patch.object(chat, "_get_openai_client", return_value=FakeClient(FakeResponse("SECRET-response-model", "SECRET-response-content"))),
            patch.dict(os.environ, {"OTEL_RESOURCE_ATTRIBUTES": "secret=SECRET-resource-env", "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST": ".*"}),
        ):
            response = await self.client.post("/v1/chat/completions?token=SECRET-query", headers=HEADERS, json={"messages": [{"role": "user", "content": "SECRET-prompt"}], "routing": {"workload": "chat", "reasoning": "none", "execution_scope": "local_only"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.spans()), 1)
        span = self.spans()[0]
        self.assertEqual(span.kind, SpanKind.SERVER)
        self.assertEqual(span.context.trace_id, int(TRACE_ID, 16))
        self.assertEqual(span.parent.span_id, int(PARENT_ID, 16))
        self.assertEqual(span.name, "POST /v1/chat/completions")
        self.assertEqual(dict(span.attributes), {
            "http.request.method": "POST", "http.route": "/v1/chat/completions", "http.response.status_code": 200,
            "gateway.workload": "chat", "gateway.execution_scope": "local_only",
            "gateway.provider": "npu-chat", "gateway.model": "npu-model", "gateway.routing_reason": "preferred",
        })
        self.assert_safe()

    async def test_current_context_invalid_routing_and_arbitrary_task_type_are_not_exported(self):
        from routes import chat

        inherited = trace.NonRecordingSpan(SpanContext(1, 2, False, TraceFlags(1), TraceState([("private", "SECRET")])))
        inherited_context = baggage.set_baggage("private", "SECRET", context=trace.set_span_in_context(inherited))
        inherited_context = context.set_value("private-runtime-value", "SECRET", context=inherited_context)
        token = context.attach(inherited_context)

        def provider_response():
            self.assertIsNone(baggage.get_baggage("private"))
            self.assertEqual(dict(baggage.get_all()), {})
            self.assertIsNone(context.get_value("private-runtime-value"))
            self.assertNotEqual(trace.get_current_span().get_span_context().trace_id, 1)
            return FakeResponse("npu-model")

        try:
            with patch.object(chat, "DEPLOY_MODE", "local-gpu"), patch.object(chat, "_get_openai_client", return_value=FakeClient(provider_response)):
                response = await self.client.post("/v1/chat/completions", headers={"Authorization": HEADERS["Authorization"]}, json={"messages": [{"role": "user", "content": "hello"}], "extra_body": {"task_type": "SECRET-arbitrary-task"}, "routing": {"reasoning": "none"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(baggage.get_baggage("private"), "SECRET")
            self.assertEqual(context.get_value("private-runtime-value"), "SECRET")
            self.assertEqual(trace.get_current_span().get_span_context().trace_id, 1)
        finally:
            context.detach(token)
        self.assertIsNone(self.spans()[0].parent)
        response = await self.client.post("/v1/chat/completions", headers=HEADERS, json={"messages": [{"role": "user", "content": "hello"}], "routing": {"execution_scope": "SECRET-invalid-scope"}})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("gateway.execution_scope", self.spans()[-1].attributes)
        self.assert_safe()

    async def test_serverless_records_configured_model_and_static_provider_not_response_model(self):
        from routes import chat

        configured = ProviderResult("http://SECRET-provider-url/v1", "configured-runpod-model", "runpod-llm")
        with (
            patch.object(chat, "DEPLOY_MODE", "serverless"),
            patch.object(chat, "get_serverless_llm_provider", return_value=configured),
            patch.object(chat, "_get_openai_client", return_value=FakeClient(FakeResponse("SECRET-response-model"))),
        ):
            response = await self.client.post("/v1/chat/completions", headers=HEADERS, json={"messages": [{"role": "user", "content": "hello"}], "routing": {"execution_scope": "remote_allowed"}})
        self.assertEqual(response.status_code, 200)
        span = self.spans()[0]
        self.assertEqual(span.attributes["gateway.provider"], "runpod-llm")
        self.assertEqual(span.attributes["gateway.model"], "configured-runpod-model")
        self.assertEqual(span.attributes["gateway.routing_reason"], "preferred")
        self.assert_safe()

    async def test_serverless_failure_keeps_selected_provider_and_configured_model(self):
        from routes import chat

        configured = ProviderResult("http://SECRET-provider-url/v1", "configured-runpod-model", "runpod-llm")
        with (
            patch.object(chat, "DEPLOY_MODE", "serverless"),
            patch.object(chat, "get_serverless_llm_provider", return_value=configured),
            patch.object(chat, "_get_openai_client", return_value=FakeClient(RuntimeError("SECRET-upstream-failure"))),
        ):
            response = await self.client.post("/v1/chat/completions", headers=HEADERS, json={"messages": [{"role": "user", "content": "hello"}], "routing": {"execution_scope": "remote_allowed"}})
        self.assertEqual(response.status_code, 503)
        span = self.spans()[0]
        self.assertEqual(span.attributes["gateway.provider"], "runpod-llm")
        self.assertEqual(span.attributes["gateway.model"], "configured-runpod-model")
        self.assertEqual(span.attributes["gateway.routing_reason"], "preferred")
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assert_safe()

    async def test_shared_ocr_records_actual_configured_ocr_model_instead_of_gpu_llm_model(self):
        from routes import chat, media

        policy = _policy()
        policy["providers"]["gpu-llm"]["workloads"].append("ocr")
        policy["providers"]["gpu-llm"]["model_default"] = "configured-llm-model"
        policy["routes"]["ocr"] = ["gpu-llm"]
        path = Path(self.temp.name) / "ocr-policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        pool = ProviderPool(path)
        pool.states["gpu-llm"].health = "healthy"
        original_client = httpx.AsyncClient
        posted = []

        def upstream(request):
            self.assertEqual(pool.states["gpu-llm"].inflight, 1)
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "SECRET-response-model", "choices": [{"message": {"content": "SECRET-ocr-content"}}]})

        body = {"model": "SECRET-request-model", "messages": [{"role": "user", "content": [{"type": "text", "text": "SECRET-ocr-prompt"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/YWJj"}}]}], "extra_body": {"task_type": "ocr"}}
        try:
            with (
                patch.object(main.app.state, "provider_pool", pool),
                patch.object(chat, "DEPLOY_MODE", "local-gpu"), patch.object(media, "DEPLOY_MODE", "local-gpu"),
                patch.object(media, "OCR_BASE_URL", "http://gpu"), patch.object(media, "OCR_MODEL_NAME", "configured-ocr-model"),
                patch.object(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(upstream))),
            ):
                response = await self.client.post("/v1/chat/completions", headers=HEADERS, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(posted[0]["model"], "configured-ocr-model")
            self.assertEqual(pool.states["gpu-llm"].inflight, 0)
            span = self.spans()[0]
            self.assertEqual(span.attributes["gateway.provider"], "gpu-llm")
            self.assertEqual(span.attributes["gateway.model"], "configured-ocr-model")
            self.assertEqual(span.attributes["gateway.workload"], "ocr")
            self.assert_safe()
        finally:
            await pool.close()

    async def test_health_unknown_routes_and_disabled_tracer_create_no_spans(self):
        for path in ("/health", "/health/liveliness", "/health/readiness", "/unknown/SECRET-path?private=SECRET-query"):
            await self.client.get(path, headers=HEADERS)
        self.assertEqual(self.spans(), ())
        with patch.object(telemetry, "_tracer", None):
            await self.client.post("/v1/embeddings", headers=HEADERS, json={"input": []})
        self.assertEqual(self.spans(), ())

    async def test_legacy_serverless_media_and_embedding_omit_scope_but_explicit_denial_keeps_it(self):
        from routes import chat, embeddings, media
        from fastapi.responses import JSONResponse

        cases = (
            ("/v1/chat/completions", {"messages": [], "extra_body": {"task_type": "asr", "audio_base64": "YWJj"}}, "asr"),
            ("/v1/embeddings", {"input": "hello"}, "embedding"),
        )
        with (
            patch.object(chat, "DEPLOY_MODE", "serverless"),
            patch.object(embeddings, "DEPLOY_MODE", "serverless"),
            patch.object(media, "handle_media_request", AsyncMock(return_value=JSONResponse({"ok": True}))) as media_call,
            patch.object(embeddings, "_handle_serverless", AsyncMock(return_value=JSONResponse({"data": []}))) as embed_call,
        ):
            for path, body, workload in cases:
                response = await self.client.post(path, headers=HEADERS, json=body)
                self.assertEqual(response.status_code, 200)
                span = self.spans()[-1]
                self.assertEqual(span.attributes["gateway.workload"], workload)
                self.assertNotIn("gateway.execution_scope", span.attributes)
                media_call.reset_mock()
                embed_call.reset_mock()
                response = await self.client.post(path, headers=HEADERS, json={**body, "routing": {"execution_scope": "local_only"}})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.spans()[-1].attributes["gateway.execution_scope"], "local_only")
                media_call.assert_not_awaited()
                embed_call.assert_not_awaited()
        self.assert_safe()

    async def test_errors_record_only_exception_type_and_other_method_is_static(self):
        async def broken(_scope, _receive, _send):
            raise RuntimeError("SECRET-error-with-body-and-key")

        middleware = telemetry.GatewayTracingMiddleware(broken)
        with self.assertRaises(RuntimeError):
            await middleware({"type": "http", "method": "SECRET-method", "path": "/v1/embeddings", "query_string": b"SECRET", "headers": []}, AsyncMock(), AsyncMock())
        span = self.spans()[0]
        self.assertEqual(span.name, "OTHER /v1/embeddings")
        self.assertEqual(span.attributes["error.type"], "RuntimeError")
        self.assert_safe()

    async def call_stream(self, version, cancelled, timeout_mode=None):
        from routes import chat

        body = json.dumps({"messages": [{"role": "user", "content": "SECRET-stream-prompt"}], "stream": True, "routing": {"reasoning": "none"}}).encode()
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions", "query_string": b"token=SECRET", "headers": [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()], "scheme": "http", "server": ("gateway", 80), "client": ("127.0.0.1", 1), "http_version": "1.1", "asgi": {"version": "3.0", "spec_version": version}}
        read = False
        first_sent = asyncio.Event()
        sent = []

        async def receive():
            nonlocal read
            if not read:
                read = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                first_sent.set()
                if timeout_mode == "slow_send":
                    await asyncio.Event().wait()

        stream = FakeStream(FakeChunk("SECRET-response-model", "SECRET-response-content"), *( ["WAIT"] if cancelled or timeout_mode == "hung" else [FakeChunk("SECRET-response-model", "done")] ))
        if timeout_mode == "continuous":
            class Continuous(FakeStream):
                count = 0

                async def __anext__(self):
                    self.count += 1
                    if self.count > 100_000:
                        raise RuntimeError("continuous safety cap")
                    return FakeChunk("SECRET-response-model", "SECRET-response-content")

            stream = Continuous()
        original_response = chat._stream_response

        def response_factory(*args, **kwargs):
            response = original_response(*args, **kwargs)
            if timeout_mode == "expired":
                response.deadline = asyncio.get_running_loop().time() - 1
            return response

        with (
            patch.object(chat, "DEPLOY_MODE", "local-gpu"),
            patch.object(chat, "_get_openai_client", return_value=FakeClient(stream)),
            patch.object(chat, "_stream_response", side_effect=response_factory),
            patch.object(self.pool, "settings", replace(self.pool.settings, request_timeout_seconds=0.15 if timeout_mode else 1)),
        ):
            task = asyncio.create_task(main.app(scope, receive, send))
            try:
                await asyncio.wait_for(first_sent.wait(), timeout=1)
                if cancelled:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=1)
                else:
                    await asyncio.wait_for(task, timeout=1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(sent[0]["status"], 503 if timeout_mode == "expired" else 200)
        self.assertTrue(stream.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)
        return sent

    async def test_real_stream_success_and_cancellation_end_one_safe_span_for_both_asgi_versions(self):
        for version in ("2.3", "2.4"):
            for cancelled in (False, True):
                before = len(self.spans())
                await self.call_stream(version, cancelled)
                self.assertEqual(len(self.spans()), before + 1)
                span = self.spans()[-1]
                self.assertEqual(span.attributes["gateway.model"], "npu-model")
                self.assertEqual(span.attributes["gateway.provider"], "npu-chat")
                if cancelled:
                    self.assertEqual(span.attributes["error.type"], "CancelledError")
                else:
                    self.assertNotEqual(span.status.status_code, StatusCode.ERROR)
                    self.assertNotIn("gateway.error_type", span.attributes)
                self.assert_safe([span])

    async def test_post_header_deadline_marks_http_200_span_as_timed_out_without_done(self):
        for version in ("2.3", "2.4"):
            for timeout_mode in ("slow_send", "hung", "continuous", "expired"):
                before = len(self.spans())
                sent = await self.call_stream(version, False, timeout_mode)
                self.assertEqual(len(self.spans()), before + 1)
                span = self.spans()[-1]
                self.assertEqual(span.status.status_code, StatusCode.ERROR)
                self.assertEqual(span.attributes["gateway.error_type"], "timed_out")
                self.assertEqual(span.attributes["http.response.status_code"], 503 if timeout_mode == "expired" else 200)
                self.assertNotIn("error.type", span.attributes)
                body = b"".join(message.get("body", b"") for message in sent)
                self.assertNotIn(b"[DONE]", body)
                self.assert_safe([span])

    async def test_typed_client_error_stays_non_error_and_upstream_error_is_classified(self):
        from routes import embeddings

        response = await self.client.post("/v1/embeddings", headers=HEADERS, json={"input": []})
        self.assertEqual(response.status_code, 400)
        span = self.spans()[-1]
        self.assertEqual(span.attributes["gateway.error_type"], "invalid_request")
        self.assertNotEqual(span.status.status_code, StatusCode.ERROR)
        with patch.object(embeddings, "DEPLOY_MODE", "serverless"), patch.object(embeddings, "_handle_serverless", AsyncMock(side_effect=RuntimeError("SECRET-upstream-exception"))):
            response = await self.client.post("/v1/embeddings", headers=HEADERS, json={"input": "hello"})
        self.assertEqual(response.status_code, 502)
        span = self.spans()[-1]
        self.assertEqual(span.attributes["gateway.error_type"], "upstream_error")
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertNotIn("error.type", span.attributes)
        self.assert_safe()

    async def test_lifespan_uses_explicit_resource_exporter_and_shutdown_without_global_changes(self):
        exporter = InMemorySpanExporter()
        global_provider = trace.get_tracer_provider()
        with (
            patch.object(telemetry, "_tracer_provider", None), patch.object(telemetry, "_tracer", None),
            patch.object(telemetry, "OTLPSpanExporter", return_value=exporter) as factory,
            patch.object(exporter, "shutdown", wraps=exporter.shutdown) as shutdown,
            patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "OTEL_RESOURCE_ATTRIBUTES": "secret=SECRET-resource-env", "OTEL_SERVICE_NAME": "asr-ai-gateway", "ENVIRONMENT": "test"}),
            patch.object(main, "DEPLOY_MODE", "serverless"),
            patch.object(main, "close_openai_clients", AsyncMock()), patch.object(main, "close_async_redis", AsyncMock()),
        ):
            async with main.lifespan(main.app):
                provider = telemetry._tracer_provider
                self.assertEqual(set(provider.resource.attributes), {"service.name", "service.version", "deployment.environment"})
                await self.client.post("/v1/embeddings", headers=HEADERS, json={"input": []})
                await asyncio.to_thread(provider.force_flush, 1000)
                self.assert_safe(exporter.get_finished_spans())
                self.assertIs(trace.get_tracer_provider(), global_provider)
            self.assertIsNone(telemetry._tracer)
            shutdown.assert_called_once()
            factory.assert_called_once_with(endpoint="http://collector:4318/v1/traces")

    async def test_missing_endpoint_and_setup_failure_are_disabled_without_private_logs(self):
        with patch.object(telemetry, "_tracer_provider", None), patch.object(telemetry, "_tracer", None):
            with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}), patch.object(telemetry, "OTLPSpanExporter") as factory:
                telemetry.setup_telemetry()
                factory.assert_not_called()
                self.assertIsNone(telemetry._tracer)
            with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://SECRET-endpoint"}), patch.object(telemetry, "OTLPSpanExporter", side_effect=RuntimeError("SECRET-setup-details")), self.assertLogs("middleware.telemetry", level="WARNING") as logs:
                telemetry.setup_telemetry()
            self.assertIsNone(telemetry._tracer)
            self.assertNotIn("SECRET", "\n".join(logs.output))
            self.assertNotIn("Traceback", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
