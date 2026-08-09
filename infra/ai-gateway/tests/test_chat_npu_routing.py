import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai

from routes import chat


def _connection_error():
    return openai.APIConnectionError(
        request=httpx.Request("POST", "http://npu:52625/v1/chat/completions")
    )


class NpuRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_local_client_uses_bounded_timeout_without_sdk_retries(self):
        chat._openai_clients.clear()
        with patch.object(chat, "AsyncOpenAI") as client:
            chat._get_openai_client("http://npu:52625/v1", "none", 60)

        client.assert_called_once_with(
            base_url="http://npu:52625/v1",
            api_key="none",
            timeout=60,
            max_retries=0,
        )
        chat._openai_clients.clear()

    def test_only_simple_tier_uses_npu(self):
        with patch.object(chat, "NPU_LLM_BASE_URL", "http://npu:52625"):
            self.assertEqual(
                chat._local_llm_target("tier-simple"),
                (
                    "http://npu:52625",
                    chat.NPU_LLM_MODEL_NAME,
                    chat.NPU_LLM_REQUEST_TIMEOUT,
                ),
            )
            self.assertEqual(
                chat._local_llm_target("tier-recap"),
                (
                    chat.LLM_BASE_URL,
                    chat.LLM_MODEL_NAME,
                    chat.LLM_REQUEST_TIMEOUT,
                ),
            )

    async def test_npu_failure_before_response_falls_back_to_gpu(self):
        error = _connection_error()
        with (
            patch.object(chat, "NPU_LLM_BASE_URL", "http://npu:52625"),
            patch.object(
                chat,
                "_call_local_llm",
                new=AsyncMock(side_effect=[error, "gpu-response"]),
            ) as call,
        ):
            result = await chat._handle_local_llm_container(
                {"model": "tier-simple", "messages": []}, True
            )

        self.assertEqual(result, "gpu-response")
        self.assertEqual(call.await_count, 2)
        self.assertEqual(
            call.await_args_list[1].args[2:],
            (
                chat.LLM_BASE_URL,
                chat.LLM_MODEL_NAME,
                chat.LLM_REQUEST_TIMEOUT,
            ),
        )

    async def test_stream_is_prefetched_before_response_is_returned(self):
        class BrokenStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise _connection_error()

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=BrokenStream()))
            )
        )
        with patch.object(chat, "_get_openai_client", return_value=client):
            with self.assertRaises(openai.APIConnectionError):
                await chat._call_local_llm(
                    {"model": "tier-simple", "messages": []},
                    True,
                    "http://npu:52625",
                    "gemma4-it:e2b",
                    60,
                )

    async def test_midstream_failure_emits_explicit_error_event(self):
        first_chunk = SimpleNamespace(
            model_dump=lambda: {"choices": [{"delta": {"content": "hello"}}]}
        )

        class FailingStream:
            def __init__(self):
                self.first = True

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.first:
                    self.first = False
                    return first_chunk
                raise _connection_error()

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=FailingStream()))
            )
        )
        with patch.object(chat, "_get_openai_client", return_value=client):
            response = await chat._call_local_llm(
                {"model": "tier-simple", "messages": []},
                True,
                "http://npu:52625",
                "gemma4-it:e2b",
                60,
            )

        chunks = [chunk async for chunk in response.body_iterator]
        error_event = json.loads(chunks[-1].removeprefix("data: ").strip())
        self.assertEqual(error_event["error"]["type"], "upstream_stream_error")
        self.assertNotIn("[DONE]", "".join(chunks))


if __name__ == "__main__":
    unittest.main()
