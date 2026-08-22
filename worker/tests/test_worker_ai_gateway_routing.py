from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from worker.config import WorkerSettings
from worker.pipelines.llm import ai_gateway_client as gateway


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


def test_worker_settings_preserves_compose_gateway_url(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_URL", "http://ai-gateway:4000")

    assert WorkerSettings(_env_file=None).ai_gateway_url == "http://ai-gateway:4000"


def test_worker_client_sends_summary_profile(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _response()

    client = SimpleNamespace(
        base_url="http://gateway/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
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


def test_worker_auto_requires_routing():
    with pytest.raises(gateway.AIGatewayClientError, match="RoutingProfile"):
        gateway.request_ai_gateway_completion(
            settings=_settings(),
            model="auto",
            messages=[{"role": "user", "content": "hello"}],
        )


def test_worker_retries_overloaded_using_retry_after(monkeypatch):
    calls = 0
    sleeps = []

    def create(**_kwargs):
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
        return _response()

    client = SimpleNamespace(
        base_url="http://gateway/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr(gateway, "get_openai_client", lambda *_: client)
    monkeypatch.setattr(gateway.time, "sleep", sleeps.append)

    assert gateway.request_ai_gateway_completion(
        settings=_settings(),
        model="auto",
        routing={
            "workload": "summary",
            "reasoning": "low",
            "execution_scope": "local_only",
        },
        messages=[{"role": "user", "content": "hello"}],
        max_retry_time=5,
    ) == "answer"
    assert calls == 2
    assert sleeps == [4.0]
