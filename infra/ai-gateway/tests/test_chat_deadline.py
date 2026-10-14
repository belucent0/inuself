import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import ClientDisconnect

from routes import chat
from services.provider_pool import ProviderPool
from services.routing import ProviderResult
from test_chat_routing import FakeChunk, FakeClient, FakeResponse, FakeStream
from test_provider_pool import _policy
from utils import upstream


class CountedStream(FakeStream):
    def __init__(self, *items, slow_close=False):
        super().__init__(*items)
        self.close_calls, self.close_cancelled = 0, False
        self.slow_close = slow_close

    async def close(self):
        self.close_calls += 1
        try:
            if self.slow_close:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise
        finally:
            self.closed = True


class GatedCloseStream(CountedStream):
    def __init__(self, *items):
        super().__init__(*items)
        self.closing, self.close_gate = asyncio.Event(), asyncio.Event()
        self.close_completed = False

    async def close(self):
        self.close_calls += 1
        self.closing.set()
        await self.close_gate.wait()
        self.closed = self.close_completed = True


class ChatDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        policy = _policy(admission=1, first_output=1)
        policy["settings"]["request_timeout_seconds"] = 0.3
        path = Path(self.temp.name) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        self.pool = ProviderPool(path)
        self.pool.states["npu-chat"].health = "healthy"
        self.pool.states["gpu-llm"].health = "healthy"
        self.profile = {"workload": "chat", "reasoning": "none", "execution_scope": "local_only"}

    async def asyncTearDown(self):
        await self.pool.close()
        self.temp.cleanup()

    def assert_timeout(self, response):
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body)["error"]["type"], "timed_out")
        self.assertEqual(response.headers["x-should-retry"], "false")

    def assert_slots_free(self):
        self.assertEqual(self.pool.states["npu-chat"].inflight, 0)
        self.assertEqual(self.pool.states["gpu-llm"].inflight, 0)

    async def local(self, client, *, stream=False):
        with patch.object(chat, "_get_openai_client", return_value=client):
            return await asyncio.wait_for(chat._handle_local(
                {"messages": [{"role": "user", "content": "hello"}], "stream": stream},
                "auto", self.profile, self.pool,
            ), timeout=2)

    async def response(self, stream):
        return await self.local(FakeClient(stream), stream=True)

    async def never_receive(self):
        await asyncio.Event().wait()

    async def test_queue_is_inside_same_deadline_and_does_not_open_upstream(self):
        npu = await self.pool.acquire("chat", "none", "local_only")
        gpu = await self.pool.acquire("chat", "none", "local_only")
        client = FakeClient(FakeResponse("unused"))
        try:
            self.assert_timeout(await self.local(client))
            self.assertEqual(client.completions.calls, [])
        finally:
            await npu.release()
            await gpu.release()
        self.assert_slots_free()

    async def test_failover_nonstream_shares_one_budget_and_sdk_cache_disables_retry(self):
        async def fail_after_delay():
            await asyncio.sleep(0.2)
            raise RuntimeError("primary failed")

        async def delayed_success():
            await asyncio.sleep(0.2)
            return FakeResponse("gpu-model")

        client = FakeClient(fail_after_delay, delayed_success)
        self.assert_timeout(await self.local(client))
        self.assertEqual(len(client.completions.calls), 2)
        self.assert_slots_free()
        with patch.object(chat, "AsyncOpenAI") as sdk:
            chat._openai_clients.clear()
            first = chat._get_openai_client("http://provider", "secret", 300)
            self.assertIs(first, chat._get_openai_client("http://provider", "secret", 300))
            sdk.assert_called_once_with(base_url="http://provider", api_key="secret", timeout=300, max_retries=0)
            chat._openai_clients.clear()

    async def test_serverless_failover_shares_budget(self):
        async def fail():
            await asyncio.sleep(0.2)
            raise RuntimeError("primary failed")

        async def slow():
            await asyncio.sleep(0.2)
            return FakeResponse("codex-model")

        client = FakeClient(fail, slow)
        with (
            patch.object(chat, "_POLICY_SETTINGS", replace(chat._POLICY_SETTINGS, request_timeout_seconds=0.3)),
            patch.object(chat, "get_serverless_llm_provider", return_value=ProviderResult("http://runpod/v1", "runpod-model", "runpod")),
            patch.object(chat, "get_codex_provider", return_value=ProviderResult("http://codex/v1", "codex-model", "codex")),
            patch.object(chat, "_get_openai_client", return_value=client),
        ):
            response = await asyncio.wait_for(chat._handle_serverless(
                {"messages": [{"role": "user", "content": "hello"}]}, "auto",
                {**self.profile, "reasoning": "high", "execution_scope": "remote_allowed"},
            ), timeout=2)
        self.assert_timeout(response)
        self.assertEqual(len(client.completions.calls), 2)

    async def test_accounting_cancel_and_failures_always_release_lease(self):
        for callback, client in (("record_success", FakeClient(FakeResponse("npu-model"))), ("record_failure", FakeClient(RuntimeError("upstream failure")))):
            with patch.object(self.pool, callback, AsyncMock(side_effect=RuntimeError("accounting failure"))):
                with self.assertRaisesRegex(RuntimeError, "accounting failure"):
                    await self.local(client)
            self.assert_slots_free()
        started = asyncio.Event()

        async def wait(*_args):
            started.set()
            await asyncio.Event().wait()

        with patch.object(self.pool, "record_success", side_effect=wait):
            task = asyncio.create_task(self.local(FakeClient(FakeResponse("npu-model"))))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_slots_free()

    async def test_expired_before_stream_start_returns_503_after_releasing_unstarted_stream(self):
        for version in ("2.3", "2.4"):
            stream = CountedStream(FakeChunk("npu-model"))
            response = await self.response(stream)
            response.deadline = asyncio.get_running_loop().time() - 1
            sent = []

            async def send(message):
                self.assert_slots_free()
                sent.append(message)

            await response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, send)
            self.assertEqual(sent[0]["status"], 503)
            self.assertEqual(json.loads(sent[1]["body"])["error"]["type"], "timed_out")
            self.assertIn((b"x-should-retry", b"false"), sent[0]["headers"])
            self.assertEqual(stream.close_calls, 1)

    async def test_slow_response_start_and_body_send_expire_and_close_once_for_both_asgi_versions(self):
        for version in ("2.3", "2.4"):
            for blocked in ("http.response.start", "http.response.body"):
                with self.subTest(version=version, blocked=blocked):
                    stream = CountedStream(FakeChunk("npu-model"), FakeChunk("npu-model", "second"))
                    response = await self.response(stream)
                    sent = []

                    async def send(message):
                        sent.append(message)
                        if message["type"] == blocked:
                            await asyncio.Event().wait()

                    await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, send), timeout=2)
                    self.assertEqual(sent[0]["status"], 200)
                    self.assertEqual(stream.close_calls, 1)
                    self.assert_slots_free()

    async def test_upstream_stall_after_first_chunk_cannot_fallback_and_releases_for_both_asgi_versions(self):
        for version in ("2.3", "2.4"):
            stream = CountedStream(FakeChunk("npu-model"), "WAIT")
            client = FakeClient(stream, FakeResponse("unused-fallback"))
            response = await self.local(client, stream=True)
            sent = []

            async def send(message):
                sent.append(message)

            await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, send), timeout=2)
            self.assertEqual(sent[0]["status"], 200)
            self.assertEqual(len(client.completions.calls), 1)
            self.assertEqual(stream.close_calls, 1)
            self.assert_slots_free()

    async def test_cancellation_during_send_and_pre_iteration_disconnect_release_for_both_versions(self):
        for version in ("2.3", "2.4"):
            stream = CountedStream(FakeChunk("npu-model"))
            response = await self.response(stream)
            started = asyncio.Event()

            async def send(_message):
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, send))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
            self.assertEqual(stream.close_calls, 1)
            self.assert_slots_free()

        stream = CountedStream(FakeChunk("npu-model"))
        response = await self.response(stream)

        async def disconnect():
            return {"type": "http.disconnect"}

        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, disconnect, AsyncMock())
        self.assertEqual(stream.close_calls, 1)
        self.assert_slots_free()
        stream = CountedStream(FakeChunk("npu-model"))
        response = await self.response(stream)
        with self.assertRaises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, self.never_receive, AsyncMock(side_effect=OSError("disconnected")))
        self.assertEqual(stream.close_calls, 1)
        self.assert_slots_free()

    async def test_slow_upstream_close_is_bounded_and_slot_returns(self):
        stream = CountedStream(FakeChunk("npu-model"), slow_close=True)
        response = await self.response(stream)
        with patch.object(upstream, "_STREAM_CLOSE_TIMEOUT", 0.05):
            await asyncio.wait_for(response.finish(), timeout=1)
        self.assertTrue(stream.close_cancelled)
        self.assertEqual(stream.close_calls, 1)
        self.assert_slots_free()
        await response.finish()
        self.assertEqual(stream.close_calls, 1)

    async def test_stream_accounting_wait_cannot_hold_slot_past_deadline(self):
        for version in ("2.3", "2.4"):
            for callback in ("record_success", "record_failure"):
                gate, started = asyncio.Event(), asyncio.Event()

                async def blocked(*_args):
                    started.set()
                    await gate.wait()

                stream = CountedStream(FakeChunk("npu-model"), *([RuntimeError("upstream failure")] if callback == "record_failure" else []))
                response = await self.response(stream)
                with patch.object(self.pool, callback, side_effect=blocked):
                    task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, AsyncMock()))
                    try:
                        await asyncio.wait_for(started.wait(), timeout=1)
                        await asyncio.wait_for(asyncio.shield(task), timeout=1)
                    finally:
                        gate.set()
                        await asyncio.wait_for(task, timeout=1)
                self.assertEqual(stream.close_calls, 1)
                self.assert_slots_free()

    async def test_native_cancellation_waits_for_shared_delayed_close_before_propagating(self):
        stream = GatedCloseStream(FakeChunk("npu-model"))
        response = await self.response(stream)
        task = asyncio.create_task(response.finish())
        try:
            await asyncio.wait_for(stream.closing.wait(), timeout=1)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            stream.close_gate.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        self.assertTrue(stream.close_completed)
        self.assertEqual(stream.close_calls, 1)
        self.assert_slots_free()
        await response.finish()
        self.assertEqual(stream.close_calls, 1)

    async def test_cancellation_while_release_waits_for_condition_cannot_leak_slot(self):
        stream = CountedStream(FakeChunk("npu-model"))
        response = await self.response(stream)
        entered = asyncio.Event()
        original = self.pool._release

        async def release(lease):
            entered.set()
            await original(lease)

        await self.pool._condition.acquire()
        with patch.object(self.pool, "_release", side_effect=release) as released:
            task = asyncio.create_task(response.finish())
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                self.pool._condition.release()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            self.assertEqual(released.await_count, 1)
        self.assert_slots_free()
        self.assertEqual(stream.close_calls, 1)

    async def test_repeated_asgi_cancellation_finishes_close_and_release_before_task_ends(self):
        for version in ("2.3", "2.4"):
            stream = GatedCloseStream(FakeChunk("npu-model"))
            response = await self.response(stream)
            task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": version}}, self.never_receive, AsyncMock()))
            try:
                await asyncio.wait_for(stream.closing.wait(), timeout=1)
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                stream.close_gate.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            self.assertTrue(stream.close_completed)
            self.assertEqual(stream.close_calls, 1)
            self.assert_slots_free()
        self.assertFalse(any(task.get_coro().__name__ == "_do_cleanup_stream" and not task.done() for task in asyncio.all_tasks()))

    async def test_cleanup_task_failure_or_own_cancellation_does_not_loop(self):
        async def failure():
            raise RuntimeError("cleanup failed")

        task = asyncio.create_task(failure())
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            await asyncio.wait_for(chat._await_cleanup(task), timeout=1)
        self.assertTrue(task.done())
        task = asyncio.create_task(asyncio.Event().wait())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(chat._await_cleanup(task), timeout=1)
        self.assertTrue(task.done())

    async def test_continuous_immediate_chunks_cannot_starve_deadline_clock_check(self):
        class Continuous(CountedStream):
            async def __anext__(self):
                self.count += 1
                if self.count > 100_000:
                    raise RuntimeError("continuous safety cap reached")
                return FakeChunk("npu-model")

        stream = Continuous()
        stream.count = 0
        opened = {"first": FakeChunk("npu-model"), "upstream": stream, "iterator": stream}
        deadline = asyncio.get_running_loop().time() + 0.01
        response = chat._stream_response(opened, {}, deadline=deadline)

        async def send(_message):
            return None

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, self.never_receive, send)
        self.assertLess(stream.count, 100_000)
        self.assertEqual(stream.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
