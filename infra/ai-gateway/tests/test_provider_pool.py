import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from services.provider_pool import (
    AdmissionTimeout,
    NoHealthyProvider,
    ProviderPool,
)


def _policy(*, npu_url="http://npu", admission=0.03, first_output=0.03):
    return {
        "settings": {
            "health_interval_seconds": 3600,
            "health_timeout_seconds": 0.03,
            "first_output_timeout_seconds": first_output,
            "request_timeout_seconds": 1,
            "failure_threshold": 2,
            "circuit_cooldown_seconds": 10,
            "admission_timeout_seconds": admission,
        },
        "providers": {
            "npu-chat": {
                "kind": "npu",
                "base_url_env": "__TEST_NPU_URL",
                "base_url_default": npu_url,
                "model_env": "__TEST_NPU_MODEL",
                "model_default": "npu-model",
                "api_key_env": "",
                "scope": "local",
                "max_inflight": 1,
                "health_path": "/v1/models",
                "workloads": ["chat"],
                "reasoning_min": "none",
                "reasoning_max": "none",
            },
            "gpu-llm": {
                "kind": "gpu",
                "base_url_env": "__TEST_GPU_URL",
                "base_url_default": "http://gpu",
                "model_env": "__TEST_GPU_MODEL",
                "model_default": "gpu-model",
                "api_key_env": "",
                "scope": "local",
                "max_inflight": 1,
                "health_path": "/v1/models",
                "workloads": ["chat", "summary"],
                "reasoning_min": "none",
                "reasoning_max": "high",
            },
            "codex": {
                "kind": "remote",
                "base_url_env": "__TEST_CODEX_URL",
                "base_url_default": "http://codex/v1",
                "model_env": "__TEST_CODEX_MODEL",
                "model_default": "codex-model",
                "api_key_env": "",
                "scope": "remote",
                "max_inflight": 1,
                "health_path": None,
                "workloads": ["chat"],
                "reasoning_min": "high",
                "reasoning_max": "high",
            },
        },
        "routes": {
            "chat": ["npu-chat", "gpu-llm", "codex"],
            "summary": ["gpu-llm"],
        },
    }


class ProviderPoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temps = []
        self._clients = []
        self._pools = []

    async def asyncTearDown(self):
        for pool in self._pools:
            await pool.close()
        for client in self._clients:
            await client.aclose()
        for temp in self._temps:
            temp.cleanup()

    def make_pool(self, policy=None, handler=None, clock=None):
        temp = tempfile.TemporaryDirectory()
        self._temps.append(temp)
        path = Path(temp.name) / "policy.json"
        path.write_text(json.dumps(policy or _policy()), encoding="utf-8")
        handler = handler or (
            lambda request: httpx.Response(
                200,
                json={"data": [{"id": "npu-model"}, {"id": "gpu-model"}]},
            )
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self._clients.append(client)
        kwargs = {"client": client}
        if clock:
            kwargs["clock"] = clock
        pool = ProviderPool(path, **kwargs)
        self._pools.append(pool)
        return pool

    @staticmethod
    def mark_healthy(pool):
        pool.states["npu-chat"].health = "healthy"
        pool.states["gpu-llm"].health = "healthy"

    async def test_profile_order_capacity_and_scope(self):
        pool = self.make_pool()
        self.mark_healthy(pool)

        npu = await pool.acquire("chat", "none", "local_only")
        self.assertEqual((npu.spec.name, npu.reason), ("npu-chat", "preferred"))
        gpu_overflow = await pool.acquire("chat", "none", "local_only")
        self.assertEqual(
            (gpu_overflow.spec.name, gpu_overflow.reason),
            ("gpu-llm", "capacity-overflow"),
        )
        await npu.release()
        await gpu_overflow.release()

        medium = await pool.acquire("chat", "medium", "local_only")
        self.assertEqual((medium.spec.name, medium.reason), ("gpu-llm", "preferred"))
        await medium.release()

        gpu = await pool.acquire("chat", "high", "local_only")
        codex = await pool.acquire("chat", "high", "remote_allowed")
        self.assertEqual((codex.spec.name, codex.reason), ("codex", "capacity-overflow"))
        await gpu.release()
        await codex.release()

    async def test_exclude_moves_to_next_provider(self):
        pool = self.make_pool()
        self.mark_healthy(pool)
        lease = await pool.acquire(
            "chat", "none", "local_only", frozenset({"npu-chat"})
        )
        self.assertEqual(lease.spec.name, "gpu-llm")
        await lease.release()

    async def test_circuit_single_half_open_and_inference_recovery(self):
        now = [100.0]
        pool = self.make_pool(clock=lambda: now[0])
        self.mark_healthy(pool)
        for _ in range(2):
            lease = await pool.acquire(
                "chat", "none", "local_only", frozenset({"gpu-llm"})
            )
            await pool.record_failure("npu-chat")
            await lease.release()
        self.assertGreater(pool.states["npu-chat"].circuit_open_until, now[0])
        pool.states["gpu-llm"].health = "unhealthy"
        self.assertTrue((await pool.snapshot())["npu-chat"]["circuit_open"])
        self.assertEqual((await pool.route_readiness())["chat"], "unavailable")

        now[0] += 11
        self.assertFalse((await pool.snapshot())["npu-chat"]["circuit_open"])
        self.assertEqual((await pool.route_readiness())["chat"], "ready")
        trial = await pool.acquire(
            "chat", "none", "local_only", frozenset({"gpu-llm"})
        )
        with self.assertRaises(NoHealthyProvider):
            await pool.acquire(
                "chat", "none", "local_only", frozenset({"gpu-llm"})
            )
        await pool.record_success("npu-chat")
        await trial.release()
        self.assertEqual(pool.states["npu-chat"].circuit_open_until, 0)

    async def test_probe_does_not_close_request_circuit(self):
        now = [100.0]
        pool = self.make_pool(clock=lambda: now[0])
        await pool.start()
        await pool.record_failure("gpu-llm")
        await pool.record_failure("gpu-llm")
        opened = pool.states["gpu-llm"].circuit_open_until
        await pool._probe(pool.specs["gpu-llm"])
        self.assertEqual(pool.states["gpu-llm"].circuit_open_until, opened)
        await pool.record_success("gpu-llm")
        self.assertEqual(pool.states["gpu-llm"].circuit_open_until, 0)

    async def test_admission_timeout_and_all_unhealthy(self):
        pool = self.make_pool()
        self.mark_healthy(pool)
        npu = await pool.acquire("chat", "none", "local_only")
        gpu = await pool.acquire("chat", "none", "local_only")
        with self.assertRaises(AdmissionTimeout):
            await pool.acquire("chat", "none", "local_only")
        await npu.release()
        await gpu.release()

        pool.states["npu-chat"].health = "unhealthy"
        pool.states["gpu-llm"].health = "unhealthy"
        with self.assertRaises(NoHealthyProvider):
            await pool.acquire("chat", "none", "local_only")

    async def test_admission_rechecks_at_circuit_cooldown_and_allows_one_half_open(self):
        policy = _policy(admission=0.2)
        policy["settings"]["circuit_cooldown_seconds"] = 0.02
        pool = self.make_pool(policy)
        self.mark_healthy(pool)
        npu = await pool.acquire("chat", "none", "local_only")
        await pool.record_failure("gpu-llm")
        await pool.record_failure("gpu-llm")

        started = asyncio.get_running_loop().time()
        trial = await pool.acquire("chat", "none", "local_only")
        elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(trial.spec.name, "gpu-llm")
        self.assertTrue(pool.states["gpu-llm"].half_open_probe)
        self.assertLess(elapsed, 0.15)

        waiter = asyncio.create_task(pool.acquire("chat", "none", "local_only"))
        await asyncio.sleep(0.01)
        self.assertFalse(waiter.done())
        await pool.record_success("gpu-llm")
        await trial.release()
        second = await waiter
        self.assertEqual(second.spec.name, "gpu-llm")
        await second.release()
        await npu.release()

    async def test_startup_probe_unknown_model_mismatch_and_disabled(self):
        def mismatch(request):
            return httpx.Response(200, json={"data": [{"id": "other"}]})

        pool = self.make_pool(_policy(npu_url=""), mismatch)
        self.assertEqual(pool.states["gpu-llm"].health, "unknown")
        self.assertFalse(pool.specs["npu-chat"].enabled)
        await pool.start()
        self.assertEqual(pool.states["gpu-llm"].health, "unhealthy")
        self.assertEqual((await pool.snapshot())["npu-chat"]["health"], "disabled")

    async def test_malformed_probe_payload_is_unhealthy_without_crashing_monitor(self):
        pool = self.make_pool(
            handler=lambda request: httpx.Response(200, json={"data": None})
        )
        await pool.start()
        self.assertEqual(pool.states["gpu-llm"].health, "unhealthy")
        self.assertIsNotNone(pool._monitor_task)
        self.assertFalse(pool._monitor_task.done())

    async def test_release_is_idempotent(self):
        pool = self.make_pool()
        self.mark_healthy(pool)
        lease = await pool.acquire("chat", "medium", "local_only")
        await lease.release()
        await lease.release()
        self.assertEqual(pool.states["gpu-llm"].inflight, 0)

    def test_invalid_policy_fails_at_startup(self):
        policy = _policy()
        policy["settings"]["admission_timeout_seconds"] = 0
        with self.assertRaises(RuntimeError):
            self.make_pool(policy)

        policy = _policy()
        policy["routes"]["chat"].append("missing")
        with self.assertRaises(RuntimeError):
            self.make_pool(policy)


if __name__ == "__main__":
    unittest.main()
