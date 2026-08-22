import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from routes import chat
from services.provider_pool import ProviderPool
from services.routing import ProviderResult
from test_provider_pool import _policy


class FakeChunk:
    def __init__(self, model, content="ok"):
        self.model, self.content = model, content

    def model_dump(self):
        return {"model": self.model, "choices": [{"delta": {"content": self.content}}]}


class FakeResponse(FakeChunk):
    def model_dump(self):
        return {"model": self.model, "choices": [{"message": {"content": self.content}}]}


class FakeStream:
    def __init__(self, *items, started=None):
        self.items, self.started, self.closed = list(items), started, False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.started:
            self.started.set()
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if item == "WAIT":
            await asyncio.Event().wait()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.closed = True


class StatusError(RuntimeError):
    def __init__(self, status_code):
        super().__init__(f"upstream {status_code}")
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if callable(result):
            result = result()
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result


class FakeClient:
    def __init__(self, *results):
        self.completions = FakeCompletions(*results)
        self.chat = SimpleNamespace(completions=self.completions)


class FakeRequest:
    def __init__(self, body, pool=None):
        self._body = body
        self.app = SimpleNamespace(state=SimpleNamespace(provider_pool=pool))

    async def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class ChatRoutingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "policy.json"
        path.write_text(json.dumps(_policy(first_output=0.02)), encoding="utf-8")
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"data": [{"id": "npu-model"}, {"id": "gpu-model"}]},
                )
            )
        )
        self.pool = ProviderPool(path, client=self.http)
        self.pool.states["npu-chat"].health = "healthy"
        self.pool.states["gpu-llm"].health = "healthy"

    async def asyncTearDown(self):
        await self.pool.close()
        await self.http.aclose()
        self.temp.cleanup()
        chat._openai_clients.clear()

    def profile(self, reasoning="none", scope="local_only"):
        return {"workload": "chat", "reasoning": reasoning, "execution_scope": scope}

    def test_routing_extraction_and_validation(self):
        body = {
            "routing": {"reasoning": "none"},
            "extra_body": {"routing": {"reasoning": "high"}},
        }
        self.assertEqual(chat._extract_routing(body)["reasoning"], "none")
        nested = {"extra_body": {"routing": {"reasoning": "auto"}}}
        self.assertEqual(chat._extract_routing(nested)["reasoning"], "medium")
        for routing in ({"reasoning": "extreme"}, {"unknown": True}, "bad"):
            with self.assertRaises(chat.RoutingValidationError):
                chat._extract_routing({"routing": routing})
        legacy_model = "tier-" + "simple"
        for model in (legacy_model, "gemma4-a4b", None):
            with self.assertRaises(chat.RoutingValidationError):
                chat._validate_model({"model": model})

    async def test_invalid_model_returns_openai_400(self):
        response = await chat.chat_completions(
            FakeRequest({"model": "tier-" + "simple", "messages": []}, self.pool)
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["error"]["type"], "invalid_request")

    async def test_invalid_json_body_returns_openai_400(self):
        for body in (ValueError("bad json"), []):
            response = await chat.chat_completions(FakeRequest(body, self.pool))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                json.loads(response.body)["error"]["type"],
                "invalid_request",
            )

    async def test_nonstream_profiles_headers_models_and_release(self):
        cases = [
            ("none", "npu-chat", "npu-model"),
            ("medium", "gpu-llm", "gpu-model"),
            ("high", "gpu-llm", "gpu-model"),
        ]
        for reasoning, provider, model in cases:
            with patch.object(
                chat, "_get_openai_client", return_value=FakeClient(FakeResponse(model))
            ):
                response = await chat._handle_local(
                    {"model": "auto", "messages": []},
                    "auto",
                    self.profile(reasoning),
                    self.pool,
                )
            self.assertEqual(response.headers["x-inference-provider"], provider)
            self.assertEqual(response.headers["x-inference-model"], model)
            self.assertEqual(json.loads(response.body)["model"], model)
            self.assertEqual(self.pool.states[provider].inflight, 0)

    async def test_high_remote_uses_codex_when_gpu_full(self):
        gpu = await self.pool.acquire("chat", "high", "local_only")
        with patch.object(
            chat, "_get_openai_client", return_value=FakeClient(FakeResponse("codex-model"))
        ):
            response = await chat._handle_local(
                {"model": "auto", "messages": []},
                "auto",
                self.profile("high", "remote_allowed"),
                self.pool,
            )
        await gpu.release()
        self.assertEqual(response.headers["x-inference-provider"], "codex")
        self.assertEqual(response.headers["x-routing-reason"], "capacity-overflow")

    async def test_error_taxonomy_fallback_and_circuit_count(self):
        for error, fallback in (
            (StatusError(400), False),
            (StatusError(413), False),
            (StatusError(422), False),
            (StatusError(429), True),
        ):
            client = FakeClient(error, FakeResponse("gpu-model"))
            with patch.object(chat, "_get_openai_client", return_value=client):
                response = await chat._handle_local(
                    {"model": "auto", "messages": []}, "auto", self.profile(), self.pool
                )
            if fallback:
                self.assertEqual(response.headers["x-inference-provider"], "gpu-llm")
                self.assertEqual(response.headers["x-routing-reason"], "error-fallback")
            else:
                self.assertEqual(response.status_code, 400)
                self.assertEqual(len(client.completions.calls), 1)
            self.assertEqual(self.pool.states["npu-chat"].request_failures, 0)

        for error in (StatusError(500), TimeoutError()):
            self.pool.states["npu-chat"].request_failures = 0
            client = FakeClient(error, FakeResponse("gpu-model"))
            with patch.object(chat, "_get_openai_client", return_value=client):
                response = await chat._handle_local(
                    {"model": "auto", "messages": []}, "auto", self.profile(), self.pool
                )
            self.assertEqual(response.headers["x-routing-reason"], "error-fallback")
            self.assertEqual(self.pool.states["npu-chat"].request_failures, 1)

    async def test_stream_completion_and_client_cancel_release(self):
        stream = FakeStream(FakeChunk("npu-model"), FakeChunk("npu-model", "two"))
        with patch.object(chat, "_get_openai_client", return_value=FakeClient(stream)):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        chunks = [chunk async for chunk in response.body_iterator]
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")
        self.assertTrue(stream.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)

        cancel_stream = FakeStream(FakeChunk("npu-model"), "WAIT")
        with patch.object(chat, "_get_openai_client", return_value=FakeClient(cancel_stream)):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        iterator = response.body_iterator
        await anext(iterator)
        self.pool.states["npu-chat"].request_failures = 1
        await iterator.aclose()
        self.assertTrue(cancel_stream.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)
        self.assertEqual(self.pool.states["npu-chat"].request_failures, 1)

    async def test_stream_prefetch_fallback_but_post_first_no_fallback(self):
        failed, good = FakeStream(RuntimeError("before")), FakeStream(FakeChunk("gpu-model"))
        client = FakeClient(failed, good)
        with patch.object(chat, "_get_openai_client", return_value=client):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        self.assertEqual(response.headers["x-inference-provider"], "gpu-llm")
        self.assertTrue(failed.closed)
        _ = [chunk async for chunk in response.body_iterator]

        self.pool.states["npu-chat"].request_failures = 0
        partial = FakeStream(FakeChunk("npu-model"), RuntimeError("after"))
        client = FakeClient(partial, FakeStream(FakeChunk("gpu-model")))
        with patch.object(chat, "_get_openai_client", return_value=client):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        with self.assertRaises(RuntimeError):
            _ = [chunk async for chunk in response.body_iterator]
        self.assertEqual(len(client.completions.calls), 1)
        self.assertTrue(partial.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)

    async def test_stream_success_only_after_completion_and_partial_failures_open_circuit(self):
        state = self.pool.states["npu-chat"]
        state.request_failures = 2
        state.circuit_open_until = self.pool._clock() - 1
        trial = FakeStream(FakeChunk("npu-model"))
        with patch.object(chat, "_get_openai_client", return_value=FakeClient(trial)):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        self.assertTrue(state.half_open_probe)
        self.assertNotEqual(state.circuit_open_until, 0)
        _ = [chunk async for chunk in response.body_iterator]
        self.assertFalse(state.half_open_probe)
        self.assertEqual(state.circuit_open_until, 0)
        self.assertEqual(state.request_failures, 0)

        for count in (1, 2):
            partial = FakeStream(FakeChunk("npu-model"), RuntimeError("partial"))
            with patch.object(chat, "_get_openai_client", return_value=FakeClient(partial)):
                response = await chat._handle_local(
                    {"model": "auto", "messages": [], "stream": True},
                    "auto",
                    self.profile(),
                    self.pool,
                )
            with self.assertRaises(RuntimeError):
                _ = [chunk async for chunk in response.body_iterator]
            self.assertEqual(state.request_failures, count)
        self.assertGreater(state.circuit_open_until, self.pool._clock())

    async def test_prefetch_timeout_and_cancel_close_and_release(self):
        npu, gpu = FakeStream("WAIT"), FakeStream("WAIT")
        with patch.object(chat, "_get_openai_client", return_value=FakeClient(npu, gpu)):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        self.assertEqual(response.status_code, 503)
        self.assertTrue(npu.closed and gpu.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)
        self.assertEqual(self.pool.states["gpu-llm"].inflight, 0)

        self.pool.states["npu-chat"].request_failures = 0
        self.pool.states["npu-chat"].circuit_open_until = 0
        started = asyncio.Event()
        cancelling = FakeStream("WAIT", started=started)
        with patch.object(chat, "_get_openai_client", return_value=FakeClient(cancelling)):
            task = asyncio.create_task(
                chat._handle_local(
                    {"model": "auto", "messages": [], "stream": True},
                    "auto",
                    self.profile(),
                    self.pool,
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelling.closed)
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)

    async def test_stream_creation_timeout_falls_back_and_releases(self):
        async def wait_forever():
            await asyncio.Event().wait()

        client = FakeClient(wait_forever, FakeStream(FakeChunk("gpu-model")))
        with patch.object(chat, "_get_openai_client", return_value=client):
            response = await chat._handle_local(
                {"model": "auto", "messages": [], "stream": True},
                "auto",
                self.profile(),
                self.pool,
            )
        self.assertEqual(response.headers["x-inference-provider"], "gpu-llm")
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)
        _ = [chunk async for chunk in response.body_iterator]

    async def test_explicit_codex_capacity_and_gpu_error_fallback(self):
        held = await self.pool.acquire_explicit("codex")
        with patch.object(chat, "_get_openai_client", return_value=FakeClient()):
            response = await chat._handle_local(
                {"model": "codex-low", "messages": []},
                "codex-low",
                self.profile("medium"),
                self.pool,
            )
        self.assertEqual(json.loads(response.body)["error"]["type"], "overloaded")
        await held.release()

        client = FakeClient(RuntimeError("codex down"), FakeResponse("gpu-model"))
        with patch.object(chat, "_get_openai_client", return_value=client):
            response = await chat._handle_local(
                {"model": "codex-low", "messages": []},
                "codex-low",
                self.profile("medium"),
                self.pool,
            )
        self.assertEqual(response.headers["x-inference-provider"], "gpu-llm")
        self.assertEqual(response.headers["x-routing-reason"], "error-fallback")
        self.assertEqual(client.completions.calls[0]["extra_body"]["reasoning_effort"], "low")

    async def test_serverless_auto_conditional_codex_fallback(self):
        runpod = ProviderResult("http://runpod/v1", "runpod-model", "runpod-llm")
        codex = ProviderResult("http://codex/v1", "codex-model", "codex", "high")
        client = FakeClient(RuntimeError("runpod down"), FakeResponse("codex-model"))
        with (
            patch.object(chat, "get_serverless_llm_provider", return_value=runpod),
            patch.object(chat, "get_codex_provider", return_value=codex),
            patch.object(chat, "_get_openai_client", return_value=client),
        ):
            response = await chat._handle_serverless(
                {"model": "auto", "messages": []},
                "auto",
                self.profile("high", "remote_allowed"),
            )
        self.assertEqual(response.headers["x-inference-provider"], "codex")
        self.assertEqual(response.headers["x-routing-reason"], "error-fallback")

        client = FakeClient(RuntimeError("runpod down"), FakeResponse("codex-model"))
        with (
            patch.object(chat, "get_serverless_llm_provider", return_value=runpod),
            patch.object(chat, "get_codex_provider", return_value=codex),
            patch.object(chat, "_get_openai_client", return_value=client),
        ):
            response = await chat._handle_serverless(
                {"model": "auto", "messages": []},
                "auto",
                self.profile("high", "local_only"),
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(client.completions.calls), 1)

        client = FakeClient(RuntimeError("codex down"), FakeResponse("runpod-model"))
        with (
            patch.object(chat, "get_serverless_llm_provider", return_value=runpod),
            patch.object(chat, "get_codex_provider", return_value=codex),
            patch.object(chat, "_get_openai_client", return_value=client),
        ):
            response = await chat._handle_serverless(
                {"model": "codex-high", "messages": []},
                "codex-high",
                self.profile(),
            )
        self.assertEqual(response.headers["x-inference-provider"], "runpod-llm")
        self.assertEqual(response.headers["x-routing-reason"], "error-fallback")


if __name__ == "__main__":
    unittest.main()
