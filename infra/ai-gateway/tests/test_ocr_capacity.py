import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from openai import AsyncOpenAI

import main
from routes import chat, media
from services.provider_pool import ProviderPool
from test_input_guards import image
from test_provider_pool import _policy
from utils import auth, upstream


OCR_BODY = {
    "messages": [{"role": "user", "content": [{"type": "text", "text": "read"}, image()]}],
    "extra_body": {"task_type": "ocr"}, "max_tokens": 8192, "temperature": 0.1,
}
CHAT_BODY = {"messages": [{"role": "user", "content": "hello"}], "stream": False}
RESULT = {
    "id": "completion", "object": "chat.completion", "created": 0, "model": "gpu-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "text"}, "finish_reason": "stop"}],
}


class OCRCapacityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.temp = tempfile.TemporaryDirectory()
        self.pools, self.clients, self.requests = [], [], []
        self.stack.enter_context(patch.object(auth, "GATEWAY_API_KEY", "test-key"))
        self.stack.enter_context(patch.object(chat, "DEPLOY_MODE", "local-gpu"))
        self.stack.enter_context(patch.object(media, "DEPLOY_MODE", "local-gpu"))
        self.stack.enter_context(patch.object(media, "OCR_BASE_URL", "http://gpu/v1/"))
        self.stack.enter_context(patch.object(media, "LLM_BASE_URL", "http://gpu"))
        self.api = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://gateway",
            headers={"Authorization": "Bearer test-key"},
        )

    async def asyncTearDown(self):
        self.stack.close()
        for pool in self.pools:
            await pool.close()
        for client in self.clients:
            if not client.is_closed:
                await client.aclose()
        await self.api.aclose()
        self.temp.cleanup()

    def pool(self, *, capacity=1, old_policy=False):
        policy = _policy(npu_url="")
        policy["providers"]["gpu-llm"]["max_inflight"] = capacity
        if not old_policy:
            policy["providers"]["gpu-llm"]["workloads"].append("ocr")
            policy["routes"]["ocr"] = ["gpu-llm"]
        path = Path(self.temp.name) / f"policy-{len(self.pools)}.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        pool = ProviderPool(path)
        pool.states["gpu-llm"].health = "healthy"
        self.pools.append(pool)
        self.stack.enter_context(patch.object(main.app.state, "provider_pool", pool, create=True))
        return pool

    def http(self, handler=None, *, close_gate=None, closing=None, fail_close=False):
        real_client = httpx.AsyncClient

        async def upstream_handler(request):
            self.requests.append(request)
            if handler:
                return await handler(request)
            return httpx.Response(200, json=RESULT)

        class CountedClient(real_client):
            close_calls = 0

            def __init__(client, **kwargs):
                super().__init__(**kwargs, transport=httpx.MockTransport(upstream_handler))
                self.clients.append(client)

            async def aclose(client):
                client.close_calls += 1
                try:
                    if closing:
                        closing.set()
                    if close_gate:
                        await close_gate.wait()
                    if fail_close:
                        await asyncio.Event().wait()
                finally:
                    await super(CountedClient, client).aclose()

        self.stack.enter_context(patch.object(media.httpx, "AsyncClient", CountedClient))
        return CountedClient

    async def post(self, body=OCR_BODY):
        return await self.api.post("/v1/chat/completions", json=body)

    def error(self, result, kind):
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["type"], kind)
        self.assertNotIn("gpu", result.json()["error"]["message"].lower())

    async def test_chat_and_ocr_share_gpu_capacity_and_busy_requests_never_reach_upstream(self):
        pool = self.pool(capacity=2)
        entered, release = asyncio.Event(), asyncio.Event()
        max_inflight = 0

        async def handler(_request):
            nonlocal max_inflight
            max_inflight = max(max_inflight, pool.states["gpu-llm"].inflight)
            if len(self.requests) == 2:
                entered.set()
            await release.wait()
            return httpx.Response(200, json=RESULT)

        make = self.http(handler)
        sdk_http = make()
        sdk = AsyncOpenAI(base_url="http://gpu/v1", api_key="test-key", http_client=sdk_http, max_retries=0)
        self.stack.enter_context(patch.object(chat, "_get_openai_client", return_value=sdk))
        chat_request = asyncio.create_task(self.post(CHAT_BODY))
        ocr_request = asyncio.create_task(self.post())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            busy = await self.post()
            self.error(busy, "overloaded")
            self.assertEqual(busy.headers["Retry-After"], "5")
            self.assertEqual(len(self.requests), 2)
            self.assertEqual(pool.states["gpu-llm"].inflight, 2)
        finally:
            release.set()
            result_chat, result_ocr = await asyncio.gather(chat_request, ocr_request)
            await sdk.close()
        self.assertEqual(max_inflight, pool.specs["gpu-llm"].max_inflight)
        self.assertEqual(result_chat.status_code, 200)
        self.assertEqual(result_ocr.json(), RESULT)
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)
        ocr_payload = json.loads(next(request.content for request in self.requests if "read" in request.content.decode()))
        self.assertEqual(ocr_payload, {
            "model": media.OCR_MODEL_NAME, "messages": OCR_BODY["messages"],
            "max_tokens": 8192, "temperature": 0.1,
        })
        self.assertTrue(all(request.url.path == "/v1/chat/completions" for request in self.requests))

    async def test_shared_gpu_unknown_unhealthy_or_circuit_open_fails_before_http(self):
        pool = self.pool()
        self.http()
        for health, circuit in (("unknown", 0), ("unhealthy", 0), ("healthy", pool._clock() + 10)):
            with self.subTest(health=health, circuit=circuit):
                pool.states["gpu-llm"].health = health
                pool.states["gpu-llm"].circuit_open_until = circuit
                self.error(await self.post(), "unavailable")
                self.assertEqual(self.requests, [])
                self.assertEqual(pool.states["gpu-llm"].inflight, 0)

    async def test_missing_pool_and_old_custom_policy_fail_closed_for_shared_gpu(self):
        pool = self.pool(old_policy=True)
        self.http()
        self.error(await self.post(), "unavailable")
        with patch.object(main.app.state, "provider_pool", None):
            self.error(await self.post(), "unavailable")
        self.assertEqual(self.requests, [])
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)

    async def test_separate_ocr_url_keeps_direct_call_with_or_without_pool(self):
        pool = self.pool(old_policy=True)
        pool.states["gpu-llm"].health = "unhealthy"
        self.http()
        with patch.object(media, "OCR_BASE_URL", "http://separate-ocr"):
            for value in (pool, None):
                with patch.object(main.app.state, "provider_pool", value):
                    self.assertEqual((await self.post()).json(), RESULT)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(str(request.url) == "http://separate-ocr/v1/chat/completions" for request in self.requests))
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)
        self.assertTrue(all(client.timeout.read == media.OCR_REQUEST_TIMEOUT for client in self.clients))

    async def test_serverless_keeps_its_existing_path_and_public_ocr_routing_stays_invalid(self):
        pool = self.pool()
        pool.states["gpu-llm"].health = "unhealthy"
        self.http()
        serverless = AsyncMock(return_value=media.JSONResponse(RESULT))
        with patch.object(chat, "DEPLOY_MODE", "serverless"), patch.object(media, "DEPLOY_MODE", "serverless"), patch.object(media, "_handle_ocr_serverless", serverless):
            self.assertEqual((await self.post()).json(), RESULT)
            serverless.assert_awaited_once_with(OCR_BODY)
        self.assertEqual(self.requests, [])
        invalid = await self.post({**OCR_BODY, "routing": {"workload": "ocr"}})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)

    async def test_upstream_error_failure_count_only_includes_5xx_network_and_timeout(self):
        pool = self.pool()
        status = 200
        fault = None

        async def handler(request):
            if fault:
                raise fault("SECRET-upstream-detail", request=request)
            return httpx.Response(status, content=b"SECRET-private-response")

        self.http(handler)
        for status, fault, counted in (
            *((code, None, False) for code in (200, 400, 401, 403, 404, 413, 422, 429)),
            (500, None, True), (503, None, True), (200, httpx.ConnectError, True), (200, httpx.ReadTimeout, True),
        ):
            with self.subTest(status=status, fault=fault):
                pool.states["gpu-llm"].request_failures = 0
                pool.states["gpu-llm"].circuit_open_until = 0
                with self.assertLogs("routes.media", level="ERROR") as logs:
                    result = await self.post()
                self.assertEqual(result.status_code, 502)
                self.assertEqual(pool.states["gpu-llm"].request_failures, int(counted))
                self.assertEqual(pool.states["gpu-llm"].inflight, 0)
                self.assertNotIn("SECRET", result.text + "\n".join(logs.output))
                self.assertEqual(self.clients[-1].close_calls, 1)

    async def test_cancellation_waits_for_client_close_and_returns_one_lease(self):
        pool = self.pool()
        entered, closing, close_gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handler(_request):
            entered.set()
            await asyncio.Event().wait()

        self.http(handler, close_gate=close_gate, closing=closing)
        with patch.object(pool, "_release", wraps=pool._release) as released:
            task = asyncio.create_task(self.post())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                await asyncio.wait_for(closing.wait(), 1)
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                close_gate.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            self.assertEqual(released.await_count, 1)
        self.assertEqual(self.clients[-1].close_calls, 1)
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)
        self.assertEqual(pool.states["gpu-llm"].request_failures, 0)

    async def test_client_close_timeout_is_bounded_and_returns_lease(self):
        pool = self.pool()
        self.http(fail_close=True)
        with patch.object(upstream, "_STREAM_CLOSE_TIMEOUT", 0.02):
            self.assertEqual((await asyncio.wait_for(self.post(), 1)).json(), RESULT)
        self.assertEqual(self.clients[-1].close_calls, 1)
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)

    async def test_cancellation_during_success_or_failure_accounting_still_closes_and_releases(self):
        pool = self.pool()
        status = 200

        async def handler(_request):
            return httpx.Response(status, json=RESULT)

        self.http(handler)
        for callback, status in (("record_success", 200), ("record_failure", 500)):
            with self.subTest(callback=callback):
                entered = asyncio.Event()

                async def blocked(*_args):
                    entered.set()
                    await asyncio.Event().wait()

                with patch.object(pool, callback, side_effect=blocked), patch.object(pool, "_release", wraps=pool._release) as released:
                    task = asyncio.create_task(self.post())
                    await asyncio.wait_for(entered.wait(), 1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                    self.assertEqual(released.await_count, 1)
                self.assertEqual(pool.states["gpu-llm"].inflight, 0)
                self.assertEqual(self.clients[-1].close_calls, 1)

    async def test_internal_route_readiness_reports_gpu_health_without_public_workload_change(self):
        published = ProviderPool._load_policy(None)
        self.assertEqual(published["routes"]["ocr"], ["gpu-llm"])
        self.assertIn("ocr", published["providers"]["gpu-llm"]["workloads"])
        pool = self.pool()
        self.assertEqual((await pool.route_readiness())["ocr"], "ready")
        pool.states["gpu-llm"].health = "unhealthy"
        self.assertEqual((await pool.route_readiness())["ocr"], "unavailable")
