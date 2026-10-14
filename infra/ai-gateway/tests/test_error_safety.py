import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai

import main
from routes import chat
from services.provider_pool import ProviderPool
from services.routing import ProviderResult
from test_chat_routing import FakeChunk, FakeStream
from test_provider_pool import _policy
from utils import auth


class ErrorSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_chat_provider_errors_do_not_echo_raw_exception_or_upstream_body(self):
        response = httpx.Response(400, request=httpx.Request("POST", "http://upstream"), json={"error": {"message": "SECRET-private-prompt"}})
        error = openai.BadRequestError("SECRET-api-key", response=response, body=response.json())
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=error))))
        temp = tempfile.TemporaryDirectory()
        policy = Path(temp.name) / "policy.json"
        policy.write_text(json.dumps(_policy()), encoding="utf-8")
        pool = ProviderPool(policy)
        pool.states["npu-chat"].health = "healthy"
        pool.states["gpu-llm"].health = "healthy"
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://gateway")
        try:
            with (
                patch.object(auth, "GATEWAY_API_KEY", "test-key"),
                patch.object(main.app.state, "provider_pool", pool, create=True),
                patch.object(chat, "_get_openai_client", return_value=sdk),
                patch.object(chat, "get_serverless_llm_provider", return_value=ProviderResult("runpod", "http://upstream", "model")),
                self.assertLogs("routes.chat", level="WARNING") as logs,
            ):
                for mode in ("local-gpu", "serverless"):
                    with patch.object(chat, "DEPLOY_MODE", mode):
                        result = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "SECRET-input-text"}], "routing": {"execution_scope": "remote_allowed"}}, headers={"Authorization": "Bearer test-key"})
                        self.assertEqual(result.status_code, 400)
                        self.assertEqual(result.json()["error"]["message"], "upstream rejected the request")
                        self.assertNotIn("SECRET", result.text)
                self.assertNotIn("SECRET", "\n".join(logs.output))
                self.assertNotIn("Traceback", "\n".join(logs.output))
        finally:
            await client.aclose()
            await pool.close()
            temp.cleanup()

    async def test_stream_close_and_probe_logs_do_not_include_raw_exception(self):
        class BrokenClose(FakeStream):
            async def close(self):
                raise RuntimeError("SECRET-close-detail")

        first = FakeChunk("model")
        stream = BrokenClose(RuntimeError("SECRET-stream-detail"))
        opened = {"first": first, "upstream": stream, "iterator": stream}
        with self.assertLogs("routes.chat", level="DEBUG") as logs:
            response = chat._stream_response(opened, {}, on_failure=AsyncMock())
            with self.assertRaisesRegex(RuntimeError, "^upstream stream failed after first output$"):
                _ = [chunk async for chunk in response.body_iterator]
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertNotIn("Traceback", "\n".join(logs.output))

        def fail(_request):
            raise RuntimeError("SECRET-probe-detail")

        temp = tempfile.TemporaryDirectory()
        policy = Path(temp.name) / "policy.json"
        policy.write_text(json.dumps(_policy()), encoding="utf-8")
        http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        pool = ProviderPool(policy, client=http)
        try:
            with self.assertLogs("services.provider_pool", level="WARNING") as logs:
                await pool._probe(pool.specs["npu-chat"])
            self.assertNotIn("SECRET", "\n".join(logs.output))
            self.assertNotIn("Traceback", "\n".join(logs.output))
        finally:
            await pool.close()
            await http.aclose()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
