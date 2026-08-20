import unittest
from unittest.mock import AsyncMock, patch

import httpx
import openai

from routes import chat


class NpuRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_only_simple_tier_uses_npu(self):
        with patch.object(chat, "NPU_LLM_BASE_URL", "http://npu:52625"):
            self.assertEqual(
                chat._local_llm_target("tier-simple"),
                ("http://npu:52625", chat.NPU_LLM_MODEL_NAME),
            )
            self.assertEqual(
                chat._local_llm_target("tier-recap"),
                (chat.LLM_BASE_URL, chat.LLM_MODEL_NAME),
            )

    async def test_npu_failure_falls_back_to_gpu(self):
        error = openai.APIConnectionError(
            request=httpx.Request("POST", "http://npu:52625/v1/chat/completions")
        )
        with (
            patch.object(chat, "NPU_LLM_BASE_URL", "http://npu:52625"),
            patch.object(chat, "_call_local_llm", new=AsyncMock(
                side_effect=[error, "gpu-response"]
            )) as call,
        ):
            result = await chat._handle_local_llm_container(
                {"model": "tier-simple", "messages": []}, False
            )

        self.assertEqual(result, "gpu-response")
        self.assertEqual(call.await_count, 2)
        self.assertEqual(
            call.await_args_list[1].args[2:],
            (chat.LLM_BASE_URL, chat.LLM_MODEL_NAME),
        )


if __name__ == "__main__":
    unittest.main()
