from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.services import ai_gateway_client as gateway


def _settings():
    return SimpleNamespace(
        ai_gateway_url="http://gateway",
        ai_gateway_api_key="key",
        llm_temperature=0.1,
        llm_max_tokens=100,
    )


def _response():
    message = SimpleNamespace(content=" answer ", reasoning=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def test_sync_client_sends_auto_routing(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _response()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(gateway, "get_openai_client", lambda *_: client)
    profile = {
        "workload": "summary",
        "reasoning": "low",
        "execution_scope": "local_only",
    }

    result = gateway.request_ai_gateway_completion(
        settings=_settings(),
        model="auto",
        routing=profile,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result == "answer"
    assert captured["model"] == "auto"
    assert captured["extra_body"] == {"routing": profile}


@pytest.mark.asyncio
async def test_async_client_sends_auto_routing(monkeypatch):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return _response()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(gateway, "get_async_openai_client", lambda *_: client)
    profile = {
        "workload": "chat",
        "reasoning": "none",
        "execution_scope": "local_only",
    }

    result = await gateway.request_ai_gateway_completion_async(
        settings=_settings(),
        routing=profile,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result == "answer"
    assert captured["model"] == "auto"
    assert captured["extra_body"] == {"routing": profile}


def test_auto_requires_routing():
    with pytest.raises(gateway.AIGatewayClientError, match="RoutingProfile"):
        gateway.request_ai_gateway_completion(
            settings=_settings(),
            model="auto",
            messages=[{"role": "user", "content": "hello"}],
        )


@pytest.mark.asyncio
async def test_async_auto_requires_routing():
    with pytest.raises(gateway.AIGatewayClientError, match="RoutingProfile"):
        await gateway.request_ai_gateway_completion_async(
            settings=_settings(),
            messages=[{"role": "user", "content": "hello"}],
        )


def _status_error(error_type: str, retry_after: str | None = None):
    request = httpx.Request("POST", "http://gateway/v1/chat/completions")
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(503, request=request, headers=headers)
    return APIStatusError(
        "gateway error",
        response=response,
        body={"error": {"type": error_type}},
    )


def test_sync_retries_only_typed_overload_and_honors_retry_after(monkeypatch):
    calls = 0
    sleeps = []

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error("overloaded", "7")
        return _response()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(gateway, "get_openai_client", lambda *_: client)
    monkeypatch.setattr(gateway.time, "sleep", sleeps.append)

    result = gateway.request_ai_gateway_completion(
        settings=_settings(),
        model="auto",
        routing={
            "workload": "summary",
            "reasoning": "low",
            "execution_scope": "local_only",
        },
        messages=[{"role": "user", "content": "hello"}],
        max_retry_time=10,
    )

    assert result == "answer"
    assert calls == 2
    assert sleeps == [7.0]


def test_sync_does_not_retry_unavailable(monkeypatch):
    calls = 0

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        raise _status_error("unavailable")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(gateway, "get_openai_client", lambda *_: client)

    with pytest.raises(gateway.AIGatewayClientError):
        gateway.request_ai_gateway_completion(
            settings=_settings(),
            model="auto",
            routing={
                "workload": "summary",
                "reasoning": "low",
                "execution_scope": "local_only",
            },
            messages=[{"role": "user", "content": "hello"}],
        )

    assert calls == 1
