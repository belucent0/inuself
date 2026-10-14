from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI, OpenAI

from app.agents.tools import llm_client as agent
from app.core import ai_gateway as core
from app.services import ai_gateway_client as service
from worker.pipelines.llm import ai_gateway_client as worker


MESSAGES = [{"role": "user", "content": "hello"}]
PROFILE = {"workload": "chat", "reasoning": "none", "execution_scope": "local_only"}
SETTINGS = SimpleNamespace(
    ai_gateway_url="http://gateway", ai_gateway_api_key="test-key",
    llm_temperature=0.1, llm_max_tokens=100,
)


def error_response(status, error_type, retry_after="7"):
    return httpx.Response(status, json={"error": {"type": error_type, "message": "gateway error"}}, headers={"Retry-After": retry_after})


def completion_response():
    return httpx.Response(200, json={
        "id": "completion", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": " answer "}, "finish_reason": "stop"}],
    })


@pytest.mark.parametrize("module", [service, worker], ids=["backend", "worker"])
@pytest.mark.parametrize("body, expected", [
    ({"error": {"type": "overloaded"}}, "overloaded"), ({"type": "overloaded"}, "overloaded"),
    ({"type": "unavailable"}, "unavailable"), ({"error": {"type": "unavailable"}}, "unavailable"),
    (None, None), ([], None), ({}, None), ({"error": None}, None),
    ({"error": []}, None), ({"error": "overloaded"}, None),
])
def test_error_type_accepts_gateway_and_sdk_body_shapes_and_ignores_malformed_bodies(module, body, expected):
    assert module._gateway_error_type(body) == expected


@pytest.mark.parametrize("module", [service, worker], ids=["backend-sync", "worker-sync"])
@pytest.mark.parametrize("status, error_type", [(503, "unavailable"), (400, "invalid_request")])
def test_sync_sdk_sends_one_http_request_for_non_retryable_gateway_errors(monkeypatch, module, status, error_type):
    requests = []
    clients = []

    def handler(request):
        requests.append(request)
        return error_response(status, error_type)

    def make(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    module.get_openai_client.cache_clear()
    monkeypatch.setattr(module, "OpenAI", make)
    try:
        with pytest.raises(module.AIGatewayClientError):
            module.request_ai_gateway_completion(settings=SETTINGS, messages=MESSAGES, routing=PROFILE)
        assert len(requests) == 1
        client = module.get_openai_client(SETTINGS.ai_gateway_url, SETTINGS.ai_gateway_api_key)
        assert client is clients[0] and client.max_retries == 0
        assert client.timeout == (300.0 if module is service else 120.0)
    finally:
        for client in clients:
            client.close()
        module.get_openai_client.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["core", "agent", "backend-async"])
@pytest.mark.parametrize("status, error_type", [(503, "unavailable"), (400, "invalid_request")])
async def test_async_sdk_sends_one_http_request_for_non_retryable_gateway_errors(monkeypatch, kind, status, error_type):
    requests = []
    clients = []
    module, factory = {
        "core": (core, core.get_async_openai_client),
        "agent": (agent, agent._get_async_client),
        "backend-async": (service, service.get_async_openai_client),
    }[kind]

    def handler(request):
        requests.append(request)
        return error_response(status, error_type)

    def make(**kwargs):
        client = AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    factory.cache_clear()
    monkeypatch.setattr(module, "AsyncOpenAI", make)
    monkeypatch.setenv("AI_GATEWAY_URL", "http://gateway/v1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    try:
        if kind == "core":
            for long_running in (False, True):
                client = factory(long_running)
                assert client is factory(long_running) and client.max_retries == 0
                assert client.timeout == (core.LONG_RUNNING_TIMEOUT if long_running else core.DEFAULT_TIMEOUT)
                with pytest.raises(APIStatusError):
                    await client.chat.completions.create(model="auto", messages=MESSAGES, extra_body={"routing": PROFILE})
                assert len(requests) == len(clients)
            assert len(clients) == 2 and clients[0] is not clients[1]
        else:
            call = agent.async_llm_completion if kind == "agent" else service.request_ai_gateway_completion_async
            exception = agent.LLMClientError if kind == "agent" else service.AIGatewayClientError
            with pytest.raises(exception):
                await call(settings=SETTINGS, messages=MESSAGES, routing=PROFILE)
            assert len(requests) == 1
            client = factory(SETTINGS.ai_gateway_url, SETTINGS.ai_gateway_api_key)
            assert client is clients[0] and client.max_retries == 0
            assert client.timeout == (120.0 if kind == "agent" else 300.0)
    finally:
        for client in clients:
            await client.close()
        factory.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [False, True])
@pytest.mark.parametrize("retry_after, delay", [("7", 7.0), ("40", 30.0)])
async def test_agent_explicit_overload_retry_owns_retry_after_and_three_request_limit(monkeypatch, recovers, retry_after, delay):
    requests = []
    sleeps = []
    clients = []

    def handler(request):
        requests.append(request)
        return completion_response() if recovers and len(requests) == 2 else error_response(503, "overloaded", retry_after)

    def make(**kwargs):
        client = AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    async def sleep(delay):
        sleeps.append(delay)

    agent._get_async_client.cache_clear()
    monkeypatch.setattr(agent, "AsyncOpenAI", make)
    monkeypatch.setattr(agent.asyncio, "sleep", sleep)
    try:
        if recovers:
            assert await agent.async_llm_completion(settings=SETTINGS, messages=MESSAGES, routing=PROFILE) == "answer"
        else:
            with pytest.raises(agent.LLMClientError):
                await agent.async_llm_completion(settings=SETTINGS, messages=MESSAGES, routing=PROFILE)
        assert len(requests) == (2 if recovers else 3)
        assert sleeps == ([delay] if recovers else [delay, delay])
    finally:
        for client in clients:
            await client.close()
        agent._get_async_client.cache_clear()


@pytest.mark.parametrize("module", [service, worker], ids=["backend-sync", "worker-sync"])
def test_sync_overload_retry_keeps_existing_sleep_budget_without_sdk_retries(monkeypatch, module):
    requests = []
    sleeps = []
    clients = []

    def handler(request):
        requests.append(request)
        return error_response(503, "overloaded")

    def make(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    module.get_openai_client.cache_clear()
    monkeypatch.setattr(module, "OpenAI", make)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    try:
        with pytest.raises(module.AIGatewayClientError):
            module.request_ai_gateway_completion(settings=SETTINGS, messages=MESSAGES, routing=PROFILE, max_retry_time=10)
        assert len(requests) == 2 and sleeps == [7.0]
    finally:
        for client in clients:
            client.close()
        module.get_openai_client.cache_clear()


@pytest.mark.asyncio
async def test_async_service_overload_retry_keeps_existing_sleep_budget_without_sdk_retries(monkeypatch):
    requests = []
    sleeps = []
    clients = []

    def handler(request):
        requests.append(request)
        return error_response(503, "overloaded")

    def make(**kwargs):
        client = AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    async def sleep(delay):
        sleeps.append(delay)

    service.get_async_openai_client.cache_clear()
    monkeypatch.setattr(service, "AsyncOpenAI", make)
    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    try:
        with pytest.raises(service.AIGatewayClientError):
            await service.request_ai_gateway_completion_async(settings=SETTINGS, messages=MESSAGES, routing=PROFILE, max_retry_time=10)
        assert len(requests) == 2 and sleeps == [7.0]
    finally:
        for client in clients:
            await client.close()
        service.get_async_openai_client.cache_clear()
