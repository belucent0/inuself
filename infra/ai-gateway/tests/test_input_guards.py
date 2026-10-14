import base64
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.responses import JSONResponse

import main
from starlette.requests import ClientDisconnect
from routes import chat, embeddings, media
from utils import auth, inputs


def image(data=b"\xff\xd8\xffimage", mime="jpeg"):
    return {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{base64.b64encode(data).decode()}"}}


def audio(task="asr", data=b"audio"):
    return {"messages": [], "extra_body": {"task_type": task, "audio_base64": base64.b64encode(data).decode()}}


class InputGuardsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(auth, "GATEWAY_API_KEY", "test-key"))
        for module in (chat, embeddings, media):
            self.stack.enter_context(patch.object(module, "DEPLOY_MODE", "local-gpu"))
        self.stack.enter_context(patch.object(media, "OCR_BASE_URL", "http://separate-ocr"))
        self.stack.enter_context(patch.object(main.app.state, "provider_pool", SimpleNamespace(specs={}, routes={}), create=True))
        self.success = {"model": "chat-model", "choices": [{"message": {"content": "answer"}}]}
        self.local = self.stack.enter_context(patch.object(chat, "_handle_local", AsyncMock(return_value=JSONResponse(self.success))))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://gateway",
            headers={"Authorization": "Bearer test-key"},
        )
        self.requests = []
        self.upstream_status = 200
        real_client = httpx.AsyncClient

        def upstream(request):
            self.requests.append(request)
            if self.upstream_status != 200:
                return httpx.Response(self.upstream_status, text="SECRET-key-and-private-prompt")
            if request.url.path == "/v1/audio/transcriptions":
                return httpx.Response(200, json={"text": "words", "language": "ko", "segments": [{"id": 7, "start": 0, "end": 1, "text": "words"}]})
            if request.url.path == "/diarize":
                return httpx.Response(200, json={"model": "diar-model", "segments": [{"speaker": "A", "start": 0, "end": 1}]})
            return httpx.Response(200, json={"data": [[1, 2]]} if request.url.path == "/v1/embeddings" else self.success)

        self.stack.enter_context(patch.object(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream))))

    async def asyncTearDown(self):
        self.stack.close()
        await self.client.aclose()

    async def post(self, body, embedding=False):
        return await self.client.post(
            "/v1/embeddings" if embedding else "/v1/chat/completions",
            content=json.dumps(body).encode(),
        )

    def no_upstream(self):
        self.local.assert_not_awaited()
        self.assertEqual(self.requests, [])

    async def test_body_limit_uses_actual_stream_with_missing_or_false_content_length(self):
        raw = b'{"messages":[{"role":"user","content":"hi"}]}'
        for embedding in (False, True):
            body = b'{"input":"hi"}' if embedding else raw
            path = "/v1/embeddings" if embedding else "/v1/chat/completions"
            for declared in (None, "1", str(len(body))):
                reads = []

                async def chunks():
                    for value in (body[:4], body[4:]):
                        reads.append(value)
                        yield value

                headers = {} if declared is None else {"Content-Length": declared}
                with patch.object(inputs, "MAX_BODY_BYTES", len(body)):
                    response = await self.client.post(path, content=chunks(), headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(reads), 2)
                self.local.reset_mock()
                self.requests.clear()

                with patch.object(inputs, "MAX_BODY_BYTES", len(body) - 1):
                    response = await self.client.post(path, content=chunks(), headers=headers)
                self.assertEqual(response.status_code, 413)
                self.no_upstream()

        reads = []

        async def oversized():
            reads.append(True)
            yield b"x"

        with patch.object(inputs, "MAX_BODY_BYTES", 4):
            response = await self.client.post("/v1/chat/completions", content=oversized(), headers={"Content-Length": "5"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(reads, [])

    async def test_bad_json_and_deep_json_return_400_without_upstream(self):
        for path in ("/v1/chat/completions", "/v1/embeddings"):
            baseline = b'{"input":"hello","nested":' if path == "/v1/embeddings" else b'{"messages":[{"role":"user","content":"hello"}],"nested":'
            values = (
                b"{", b"\xff", b"[]",
                baseline + b"[" * 65 + b"0" + b"]" * 65 + b"}",
                baseline + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
                baseline + b'{"nested":' * 65 + b"0" + b"}" * 65 + b"}",
            )
            for value in values:
                response = await self.client.post(path, content=value)
                self.assertEqual(response.status_code, 400)
                self.no_upstream()
            valid_body = {"input": "hello"} if path == "/v1/embeddings" else {"messages": [{"role": "user", "content": "hello"}]}
            for length in ("wrong", "-1", "1.5"):
                response = await self.client.post(path, content=json.dumps(valid_body).encode(), headers={"Content-Length": length})
                self.assertEqual(response.status_code, 400)
                self.no_upstream()

    async def test_disconnect_parser_recursion_and_duplicate_length_are_bounded_errors(self):
        async def disconnected(_request):
            yield b"{"
            raise ClientDisconnect()

        body = {"messages": [{"role": "user", "content": "hello"}]}
        with patch("starlette.requests.Request.stream", disconnected):
            self.assertEqual((await self.post(body)).status_code, 400)
        with patch.object(inputs.json, "loads", side_effect=RecursionError("SECRET")):
            response = await self.post(body)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("SECRET", response.text)
        raw = json.dumps(body).encode()
        response = await self.client.post("/v1/chat/completions", content=raw, headers=[("Content-Length", str(len(raw))), ("Content-Length", str(len(raw)))])
        self.assertEqual(response.status_code, 400)
        self.no_upstream()

    async def test_invalid_types_and_numbers_are_rejected_before_upstream(self):
        invalid = [
            {"messages": None}, {"messages": []}, {"messages": [None]}, {"messages": [{"role": []}]},
            {"messages": [{"role": "user", "content": {}}]},
            {"messages": [{"role": "user", "content": [None]}]},
            {"messages": [{"role": "user", "content": [{"type": "text", "text": None}]}]},
            {"extra_body": None}, {"extra_body": {"task_type": []}},
            {"extra_body": {"language": []}}, {"extra_body": {"accuracy_mode": []}},
            {"stream": "false"}, {"stream": 1},
            *(({"max_tokens": value}) for value in (True, 0, -1, 32769, 1.5, "8")),
            *(({"temperature": value}) for value in (True, None, "1", float("nan"), float("inf"), 10**400)),
            *(({"extra_body": {"min_speakers": value}}) for value in (True, 0, -1, 1.5, "2")),
            {"extra_body": {"min_speakers": 3, "max_speakers": 2}},
        ]
        for body in invalid:
            with self.subTest(body=body):
                payload = {"messages": [{"role": "user", "content": "hello"}], **body}
                self.assertEqual((await self.post(payload)).status_code, 400)
                self.no_upstream()
        for value in (None, 1, [1], ["text", None], {}, "", [], [""]):
            self.assertEqual((await self.post({"input": value}, embedding=True)).status_code, 400)
            self.no_upstream()
        for invalid_field in ({"model": []}, {"extra_body": None}):
            self.assertEqual((await self.post({"input": "hello", **invalid_field}, embedding=True)).status_code, 400)
            self.no_upstream()

    async def test_text_limit_counts_all_messages_parts_and_embedding_items(self):
        with patch.object(inputs, "MAX_TEXT_CHARS", 4):
            for body in (
                {"messages": [{"role": "developer", "content": "ab"}, {"role": "user", "content": [{"type": "text", "text": "cd"}]}]},
                {"input": ["ab", "cd"]},
            ):
                embedding = "input" in body
                self.assertEqual((await self.post(body, embedding)).status_code, 200)
                self.local.reset_mock()
                self.requests.clear()
                if embedding:
                    body["input"].append("e")
                else:
                    body["messages"].append({"role": "user", "content": "e"})
                self.assertEqual((await self.post(body, embedding)).status_code, 413)
                self.no_upstream()

    async def test_audio_base64_is_strict_and_bounded_before_decode_or_upstream(self):
        for task in ("asr", "diarization"):
            with patch.object(inputs, "MAX_AUDIO_BYTES", 4):
                self.assertEqual((await self.post(audio(task, b"1234"))).status_code, 200)
                self.requests.clear()
                for data in (b"12345", b"1234567"):
                    self.assertEqual((await self.post(audio(task, data))).status_code, 413)
                    self.no_upstream()
                for value in (None, True, "", "%%%", "YWJj\n", "YWJj==?", "한글"):
                    body = audio(task)
                    body["extra_body"]["audio_base64"] = value
                    self.assertEqual((await self.post(body)).status_code, 400)
                    self.no_upstream()
            with patch.object(inputs, "MAX_AUDIO_BYTES", 1), patch.object(inputs.base64, "b64decode") as decode:
                self.assertEqual((await self.post(audio(task, b"too-large"))).status_code, 413)
                decode.assert_not_called()

    async def test_images_require_inline_allowed_type_signature_and_total_budget(self):
        for url in ("https://example.test/a.png", "http://127.0.0.1/private", "file:///private", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/gif;base64,R0lGODlh"):
            body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
            self.assertEqual((await self.post(body)).status_code, 400)
            self.no_upstream()
        for part in (image(b"not-jpeg"), {"type": "image_url", "image_url": "bad"}, image(b"RIFFbad", "webp")):
            self.assertEqual((await self.post({"messages": [{"role": "user", "content": [part]}]})).status_code, 400)
            self.no_upstream()
        valid = (image(b"\xff\xd8\xffabc"), image(b"\x89PNG\r\n\x1a\n", "png"), image(b"RIFFxxxxWEBP", "webp"))
        for part in valid:
            self.assertEqual((await self.post({"messages": [{"role": "user", "content": [part]}]})).status_code, 200)
            self.requests.clear()
        body = {"messages": [{"role": "user", "content": list(valid[:2])}]}
        with patch.object(inputs, "MAX_IMAGE_BYTES", 14):
            self.assertEqual((await self.post(body)).status_code, 200)
        self.requests.clear()
        with patch.object(inputs, "MAX_IMAGE_BYTES", 13):
            self.assertEqual((await self.post(body)).status_code, 413)
            self.no_upstream()

    async def test_normal_outputs_preserve_chat_media_and_embedding_shapes(self):
        messages = [{"role": "developer", "content": "instruction"}, {"role": "assistant", "content": None, "tool_calls": []}, {"role": "user", "content": "hello"}]
        response = await self.post({"messages": messages, "max_tokens": 8192, "stream": False, "temperature": 0.1})
        self.assertEqual(response.json(), self.success)
        self.assertEqual(self.local.await_args.args[0]["messages"], messages)
        self.local.reset_mock()
        for task in ("asr", "diarization"):
            response = await self.post(audio(task))
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["object"], "chat.completion")
            result = json.loads(payload["choices"][0]["message"]["content"])
            self.assertIn("segments", result)
            if task == "asr":
                self.assertEqual(result["segments"][0]["id"], 7)
                self.assertEqual(result["text"], "words")
        response = await self.post({"messages": [{"role": "user", "content": [image()]}]})
        self.assertEqual(response.json(), self.success)
        response = await self.post({"input": ["one", "two"]}, embedding=True)
        self.assertEqual(response.json(), {"data": [[1, 2]]})

    async def test_output_token_boundaries_and_optional_audio_parameters(self):
        for tokens in (1, 32768):
            self.assertEqual((await self.post({"messages": [{"role": "user", "content": "hello"}], "max_tokens": tokens})).status_code, 200)
        for language in (None, "auto", "ko"):
            body = audio()
            body["extra_body"]["language"] = language
            self.assertEqual((await self.post(body)).status_code, 200)
        body = audio("diarization")
        body["extra_body"].update(min_speakers=1, max_speakers=2)
        self.assertEqual((await self.post(body)).status_code, 200)

    async def test_serverless_media_and_embedding_success_and_errors_are_protected(self):
        import openai

        calls = AsyncMock()
        calls.return_value = SimpleNamespace(text="words", model_dump=lambda: self.success)
        sdk = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=calls)),
            chat=SimpleNamespace(completions=SimpleNamespace(create=calls)),
            embeddings=SimpleNamespace(create=calls),
        )
        bodies = [audio(), {"messages": [{"role": "user", "content": [image()]}]}, {"input": "hello"}]
        with (
            patch.object(chat, "DEPLOY_MODE", "serverless"),
            patch.object(media, "DEPLOY_MODE", "serverless"),
            patch.object(embeddings, "DEPLOY_MODE", "serverless"),
            patch.object(openai, "AsyncOpenAI", return_value=sdk),
        ):
            for body in bodies:
                body["routing"] = {"execution_scope": "remote_allowed"}
                response = await self.post(body, "input" in body)
                self.assertEqual(response.status_code, 200)
                if "extra_body" in body:
                    self.assertEqual(json.loads(response.json()["choices"][0]["message"]["content"])["text"], "words")
                else:
                    self.assertEqual(response.json(), self.success)
            self.assertEqual(calls.await_count, 3)
            calls.reset_mock()
            calls.side_effect = RuntimeError("SECRET-key-and-private-prompt")
            with self.assertLogs(level="ERROR") as logs:
                for body in bodies:
                    response = await self.post(body, "input" in body)
                    self.assertEqual(response.status_code, 502)
                    self.assertNotIn("SECRET", response.text)
            self.assertNotIn("SECRET", "\n".join(logs.output))
            self.assertNotIn("Traceback", "\n".join(logs.output))
            calls.reset_mock()
            with patch.object(inputs, "MAX_AUDIO_BYTES", 1):
                self.assertEqual((await self.post(bodies[0])).status_code, 413)
            calls.assert_not_awaited()

    async def test_upstream_response_body_is_absent_from_errors_and_logs(self):
        self.upstream_status = 500
        bodies = [audio(), audio("diarization"), {"messages": [{"role": "user", "content": [image()]}]}, {"input": "private text"}]
        with self.assertLogs(level="ERROR") as logs:
            for body in bodies:
                response = await self.post(body, "input" in body)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json(), {"error": {"message": "upstream request failed", "type": "upstream_error"}})
                self.assertNotIn("SECRET", response.text)
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertNotIn("Traceback", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
