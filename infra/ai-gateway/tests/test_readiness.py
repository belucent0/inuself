import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import main
from services.provider_pool import ProviderPool
from test_provider_pool import _policy


class ReadinessTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "policy.json"
        path.write_text(json.dumps(_policy()), encoding="utf-8")
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        self.pool = ProviderPool(path, client=self.http)
        main.app.state.provider_pool = self.pool

    async def asyncTearDown(self):
        await self.pool.close()
        await self.http.aclose()
        self.temp.cleanup()

    async def response(self):
        with patch.object(main, "DEPLOY_MODE", "local-gpu"):
            response = await main.health_readiness()
        return response, json.loads(response.body)

    async def test_ready_degraded_unavailable_and_capacity_ignored(self):
        self.pool.states["npu-chat"].health = "healthy"
        self.pool.states["gpu-llm"].health = "healthy"
        gpu = await self.pool.acquire("chat", "medium", "local_only")
        response, body = await self.response()
        self.assertEqual((response.status_code, body["status"]), (200, "ready"))
        self.assertEqual(body["providers"]["gpu-llm"]["inflight"], 1)
        await gpu.release()

        self.pool.states["gpu-llm"].health = "unhealthy"
        response, body = await self.response()
        self.assertEqual((response.status_code, body["status"]), (200, "degraded"))
        self.assertEqual(body["routes"], {"chat": "ready", "summary": "unavailable"})

        self.pool.states["npu-chat"].health = "unhealthy"
        response, body = await self.response()
        self.assertEqual((response.status_code, body["status"]), (503, "unavailable"))

    async def test_serverless_requires_runpod_url(self):
        with (
            patch.object(main, "DEPLOY_MODE", "serverless"),
            patch.object(main, "RUNPOD_LLM_BASE_URL", ""),
        ):
            response = await main.health_readiness()
        self.assertEqual(response.status_code, 503)

        with (
            patch.object(main, "DEPLOY_MODE", "serverless"),
            patch.object(main, "RUNPOD_LLM_BASE_URL", "http://runpod"),
        ):
            response = await main.health_readiness()
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
