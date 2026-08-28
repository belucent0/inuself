from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.agents.tools import llm_client


@pytest.mark.asyncio
async def test_agent_llm_retries_typed_overload_using_retry_after(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", "http://gateway/v1/chat/completions")
            response = httpx.Response(
                503,
                request=request,
                headers={"Retry-After": "4"},
            )
            raise APIStatusError(
                "overloaded",
                response=response,
                body={"error": {"type": "overloaded"}},
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" answer "))]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_client, "_get_async_client", lambda *_args: client)

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)

    result = await llm_client.async_llm_completion(
        settings=SimpleNamespace(
            ai_gateway_url="http://gateway",
            ai_gateway_api_key="key",
            llm_temperature=0.1,
            llm_max_tokens=100,
        ),
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result == "answer"
    assert calls == 2
    assert sleeps == [4.0]
