import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

import main
from routes import chat, embeddings, media
from utils import auth


class GatewayAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = "test-gateway-secret"
        self.key_patch = patch.object(auth, "GATEWAY_API_KEY", self.key)
        self.key_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://gateway"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.key_patch.stop()

    async def test_denied_requests_never_parse_json_or_call_upstream(self):
        headers_cases = (
            [],
            [("Authorization", "Bearer wrong-key")],
            [("Authorization", "Basic test-gateway-secret")],
            [("Authorization", "Bearer")],
            [("Authorization", "Bearer ")],
            [("Authorization", "Bearer  test-gateway-secret")],
            [("Authorization", "Bearer test-gateway-secret extra")],
            [(b"authorization", b"Bearer \xff")],
            [("Authorization", f"Bearer {self.key}"), ("Authorization", "Bearer wrong-key")],
        )
        with (
            patch.object(Request, "json", AsyncMock()) as parse,
            patch.object(chat, "_handle_local", AsyncMock()) as local,
            patch.object(chat, "_handle_serverless", AsyncMock()) as remote,
            patch.object(media, "handle_media_request", AsyncMock()) as media_call,
            patch.object(embeddings.httpx, "AsyncClient") as embed_client,
            self.assertLogs("httpx", level="INFO") as logs,
        ):
            for path in ("/v1/chat/completions", "/v1/embeddings", "/health/readiness"):
                for headers in headers_cases:
                    with self.subTest(path=path, headers=headers):
                        method = self.client.get if path.startswith("/health") else self.client.post
                        kwargs = {} if path.startswith("/health") else {"content": b"{"}
                        response = await method(path, headers=headers, **kwargs)
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
                        self.assertEqual(response.json(), {"detail": "Invalid Gateway credentials"})
                        self.assertNotIn(self.key, response.text)
                        self.assertNotIn("wrong-key", response.text)
            parse.assert_not_awaited()
            local.assert_not_awaited()
            remote.assert_not_awaited()
            media_call.assert_not_awaited()
            embed_client.assert_not_called()
        self.assertNotIn(self.key, "\n".join(logs.output))
        self.assertNotIn("wrong-key", "\n".join(logs.output))

    async def test_missing_config_fails_closed_but_liveness_stays_public(self):
        for configured_key in ("", " \t\n "):
            with (
                self.subTest(configured_key=configured_key),
                patch.object(auth, "GATEWAY_API_KEY", configured_key),
                patch.object(Request, "json", AsyncMock()) as parse,
                patch.object(chat, "_handle_local", AsyncMock()) as local,
                patch.object(chat, "_handle_serverless", AsyncMock()) as remote,
                patch.object(media, "handle_media_request", AsyncMock()) as media_call,
                patch.object(embeddings.httpx, "AsyncClient") as embed_client,
            ):
                for path in ("/v1/chat/completions", "/v1/embeddings", "/health/readiness"):
                    method = self.client.get if path.startswith("/health") else self.client.post
                    response = await method(path, headers={"Authorization": f"Bearer {self.key}"})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {"detail": "Gateway authentication is not configured"})
                for path in ("/health", "/health/liveliness"):
                    response = await self.client.get(path)
                    self.assertEqual((response.status_code, response.json()), (200, {"status": "ok"}))
                parse.assert_not_awaited()
                local.assert_not_awaited()
                remote.assert_not_awaited()
                media_call.assert_not_awaited()
                embed_client.assert_not_called()

    async def test_valid_credentials_reach_chat_media_embeddings_and_readiness(self):
        headers = {"Authorization": f"bearer {self.key}"}
        success = JSONResponse({"choices": [{"message": {"content": "ok"}}]})
        with (
            patch.object(chat, "DEPLOY_MODE", "serverless"),
            patch.object(embeddings, "DEPLOY_MODE", "serverless"),
            patch.object(main, "DEPLOY_MODE", "serverless"),
            patch.object(main, "RUNPOD_LLM_BASE_URL", "http://runpod"),
            patch.object(chat, "_handle_serverless", AsyncMock(return_value=success)) as chat_call,
            patch.object(media, "handle_media_request", AsyncMock(return_value=success)) as media_call,
            patch.object(embeddings, "_handle_serverless", AsyncMock(return_value=JSONResponse({"data": []}))) as embed_call,
        ):
            chat_body = {"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True, "routing": {"execution_scope": "remote_allowed"}}
            response = await self.client.post("/v1/chat/completions", json=chat_body, headers=headers)
            self.assertEqual(response.status_code, 200)
            chat_call.assert_awaited_once()
            self.assertEqual(chat_call.await_args.args[0], chat_body)
            self.assertEqual(chat_call.await_args.args[1], "auto")
            self.assertEqual(chat_call.await_args.args[2]["execution_scope"], "remote_allowed")

            for task in ("asr", "diarization", "ocr"):
                body = {"messages": [], "extra_body": {"task_type": task}}
                response = await self.client.post("/v1/chat/completions", json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(media_call.await_args.args[0], body)
            vision_body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,dGVzdA=="}}]}]}
            response = await self.client.post("/v1/chat/completions", json=vision_body, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(media_call.await_count, 4)
            self.assertEqual(media_call.await_args.args[0]["extra_body"]["task_type"], "ocr")

            response = await self.client.post("/v1/embeddings", json={"input": "hello"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            embed_call.assert_awaited_once_with("hello", "bge-small-en-v1.5")

            response = await self.client.get("/health/readiness", headers=headers)
            self.assertEqual((response.status_code, response.json()["status"]), (200, "ready"))
            self.assertNotIn(self.key, response.text)


if __name__ == "__main__":
    unittest.main()
