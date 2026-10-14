import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.responses import JSONResponse

import main
from routes import chat, embeddings, media
from utils import auth


class ExecutionScopeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(auth, "GATEWAY_API_KEY", "test-key"))
        self.stack.enter_context(patch.object(main.app.state, "provider_pool", object(), create=True))
        success = JSONResponse({"ok": True})
        self.local = self.stack.enter_context(patch.object(chat, "_handle_local", AsyncMock(return_value=success)))
        self.remote = self.stack.enter_context(patch.object(chat, "_handle_serverless", AsyncMock(return_value=success)))
        self.media = self.stack.enter_context(patch.object(media, "handle_media_request", AsyncMock(return_value=success)))
        self.embed_remote = self.stack.enter_context(patch.object(embeddings, "_handle_serverless", AsyncMock(return_value=success)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://gateway",
            headers={"Authorization": "Bearer test-key"},
        )
        self.embed_local = AsyncMock()
        self.embed_local.post.return_value = httpx.Response(
            200, json={"ok": True}, request=httpx.Request("POST", "http://embedding")
        )
        context = AsyncMock()
        context.__aenter__.return_value = self.embed_local
        self.stack.enter_context(patch.object(embeddings.httpx, "AsyncClient", return_value=context))

    async def asyncTearDown(self):
        self.stack.close()
        await self.client.aclose()

    def reset_calls(self):
        for mock in (self.local, self.remote, self.media, self.embed_remote, self.embed_local):
            mock.reset_mock()

    def assert_no_upstream(self):
        for mock in (self.local, self.remote, self.media, self.embed_remote):
            mock.assert_not_awaited()
        self.embed_local.post.assert_not_awaited()

    def cases(self):
        return [
            ("chat", {"model": "auto", "messages": []}),
            ("codex", {"model": "codex-high", "messages": []}),
            *((task, {"messages": [], "extra_body": {"task_type": task}}) for task in ("asr", "diarization", "ocr")),
            ("vision", {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,dGVzdA=="}}]}]}),
            ("embedding", {"input": "hello"}),
        ]

    async def test_scope_applies_before_any_upstream_for_every_route_and_deploy_mode(self):
        for mode in ("local-gpu", "serverless"):
            with patch.object(chat, "DEPLOY_MODE", mode), patch.object(embeddings, "DEPLOY_MODE", mode):
                for kind, body in self.cases():
                    for scope in ("local_only", "remote_allowed", None):
                        for nested in (False, True):
                            with self.subTest(mode=mode, kind=kind, scope=scope, nested=nested):
                                self.reset_calls()
                                payload = {**body, "extra_body": dict(body.get("extra_body", {}))}
                                if scope is not None:
                                    target = payload["extra_body"] if nested else payload
                                    target["routing"] = {"execution_scope": scope}
                                path = "/v1/embeddings" if kind == "embedding" else "/v1/chat/completions"
                                response = await self.client.post(path, json=payload)
                                effective = scope or "local_only"
                                legacy_media = scope is None and kind not in ("chat", "codex")
                                denied = not legacy_media and effective == "local_only" and (mode == "serverless" or kind == "codex")
                                self.assertEqual(response.status_code, 403 if denied else 200)
                                if denied:
                                    self.assertEqual(response.json()["error"]["type"], "forbidden")
                                    self.assert_no_upstream()
                                elif kind in ("chat", "codex"):
                                    (self.local if mode == "local-gpu" else self.remote).assert_awaited_once()
                                elif kind == "embedding":
                                    (self.embed_local.post if mode == "local-gpu" else self.embed_remote).assert_awaited_once()
                                else:
                                    self.media.assert_awaited_once()

    async def test_invalid_routing_returns_400_for_chat_media_vision_and_embedding(self):
        invalid_profiles = [[], None, "bad", {"unknown": True}]
        invalid_profiles.extend(
            {field: value}
            for field in ("workload", "reasoning", "execution_scope")
            for value in ([], {}, None, True, 1, "invalid")
        )
        for mode in ("local-gpu", "serverless"):
            with patch.object(chat, "DEPLOY_MODE", mode), patch.object(embeddings, "DEPLOY_MODE", mode):
                for kind, body in self.cases():
                    for routing in invalid_profiles:
                        for nested in (False, True):
                            with self.subTest(mode=mode, kind=kind, routing=routing, nested=nested):
                                self.reset_calls()
                                payload = {**body, "extra_body": dict(body.get("extra_body", {}))}
                                target = payload["extra_body"] if nested else payload
                                target["routing"] = routing
                                path = "/v1/embeddings" if kind == "embedding" else "/v1/chat/completions"
                                response = await self.client.post(path, json=payload)
                                self.assertEqual(response.status_code, 400)
                                self.assertEqual(response.json()["error"]["type"], "invalid_request")
                                self.assert_no_upstream()

    async def test_top_level_scope_takes_precedence_over_nested_scope(self):
        with patch.object(chat, "DEPLOY_MODE", "serverless"), patch.object(embeddings, "DEPLOY_MODE", "serverless"):
            for kind, body in self.cases():
                self.reset_calls()
                payload = {**body, "routing": {"execution_scope": "local_only"}, "extra_body": {**body.get("extra_body", {}), "routing": {"execution_scope": "remote_allowed"}}}
                path = "/v1/embeddings" if kind == "embedding" else "/v1/chat/completions"
                response = await self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 403)
                self.assert_no_upstream()


if __name__ == "__main__":
    unittest.main()
