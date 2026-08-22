import json
from inspect import signature
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.controllers import (
    ai_chat_controller,
    auth_controller,
    events_controller,
    media_controller,
)
from app.core import auth
from app.core.config import get_settings


@pytest.fixture(scope="module")
def app_client():
    from app.main import create_app

    client = TestClient(create_app())
    yield client
    client.close()


def make_request(*, authorization: str | None = None, query: bytes = b"") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "query_string": query,
            "headers": headers,
        }
    )


def test_db_dependencies_close_before_streaming_or_auth_response():
    for dependency, parameter in (
        (auth.get_current_user, "session"),
        (auth_controller.check_login_id, "session"),
        (auth_controller.signup, "session"),
        (auth_controller.login, "session"),
        (ai_chat_controller.get_svc, "session"),
        (ai_chat_controller.regenerate_response, "session"),
        (ai_chat_controller.stream_message_v2, "session"),
        (media_controller.stream_media, "session"),
    ):
        assert signature(dependency).parameters[parameter].default.scope == "function"

    lifecycle = []

    async def session_dependency():
        lifecycle.append("open")
        try:
            yield object()
        finally:
            lifecycle.append("closed")

    async def endpoint(_session=Depends(session_dependency, scope="function")):
        async def body():
            assert lifecycle == ["open", "closed"]
            yield b"ok"

        return StreamingResponse(body())

    app = FastAPI()
    app.get("/")(endpoint)
    assert TestClient(app).get("/").content == b"ok"


@pytest.mark.asyncio
async def test_bearer_and_query_tokens_do_not_authenticate_without_cookie():
    class UnusedSession:
        async def execute(self, statement):
            raise AssertionError("database must not be queried without a cookie")

    for request in (
        make_request(authorization="Bearer obsolete-token"),
        make_request(query=b"access_token=obsolete-token"),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user(request, UnusedSession())
        assert exc.value.status_code == 401


def test_origin_boundary_rejects_foreign_unsafe_requests_only(app_client):
    allowed_origin = next(
        origin.strip()
        for origin in get_settings().cors_origins.split(",")
        if origin.strip()
    )
    foreign = app_client.post(
        "/api/auth/logout", headers={"Origin": "https://evil.example"}
    )
    allowed = app_client.post(
        "/api/auth/logout", headers={"Origin": allowed_origin}
    )
    absent = app_client.post("/api/auth/logout")

    assert (foreign.status_code, allowed.status_code, absent.status_code) == (
        403,
        204,
        204,
    )


def test_destructive_and_inference_routes_reject_anonymous_requests(app_client):
    responses = (
        app_client.delete("/api/contents/queued"),
        app_client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]}),
        app_client.post("/api/search", json={"query": "x"}),
        app_client.get("/api/search", params={"q": "x"}),
        app_client.get("/api/scan/wpi/questions"),
    )

    assert [response.status_code for response in responses] == [401] * 5


def test_file_progress_endpoint_rejects_anonymous_request(app_client):
    assert app_client.get("/api/events/file-progress/stream").status_code == 401


@pytest.mark.asyncio
async def test_media_owner_allowed_and_non_owner_forbidden(monkeypatch):
    owner_id = uuid4()
    content_id = uuid4()
    content = SimpleNamespace(
        user_id=owner_id,
        file=SimpleNamespace(object_key="uploads/media.mp3"),
    )

    class Repo:
        async def get_by_file_id(self, file_id):
            assert file_id == content_id
            return content

    class Cache:
        async def get_file_info(self, object_key):
            return {"size": 4}

        async def stream_file(self, object_key, start, end):
            yield b"data"

    monkeypatch.setattr(media_controller, "ContentRepository", lambda session: Repo())

    response = await media_controller.stream_media(
        content_id,
        make_request(),
        None,
        object(),
        owner_id,
        Cache(),
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"

    head = await media_controller.head_media(
        content_id,
        object(),
        owner_id,
        Cache(),
    )
    assert head.status_code == 200
    assert head.headers["cache-control"] == "private, no-store"

    with pytest.raises(HTTPException) as exc:
        await media_controller.stream_media(
            content_id,
            make_request(),
            None,
            object(),
            uuid4(),
            Cache(),
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as head_exc:
        await media_controller.head_media(
            content_id,
            object(),
            uuid4(),
            Cache(),
        )
    assert head_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_file_progress_ownership_filters_and_caches_by_file(monkeypatch):
    owner_id = uuid4()
    file_id = uuid4()

    class Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, statement):
            self.calls += 1
            return SimpleNamespace(scalar_one_or_none=lambda: owner_id)

    session = Session()

    class Factory:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(events_controller, "async_session_factory", Factory)
    cache = {}
    event = {"type": "file_progress", "file_id": str(file_id)}

    assert await events_controller._event_owned_by_user(
        event, owner_id, cache
    )
    assert await events_controller._event_owned_by_user(
        event, owner_id, cache
    )
    assert session.calls == 1
    assert not await events_controller._event_owned_by_user(
        event, uuid4(), {}
    )
    assert not await events_controller._event_owned_by_user(
        {"type": "file_progress"}, owner_id, {}
    )


@pytest.mark.asyncio
async def test_file_progress_does_not_cache_precommit_miss(monkeypatch):
    owner_id = uuid4()
    owners = iter((None, owner_id))

    class Session:
        async def execute(self, statement):
            owner = next(owners)
            return SimpleNamespace(scalar_one_or_none=lambda: owner)

    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(events_controller, "async_session_factory", Factory())
    event = {"file_id": str(uuid4())}
    cache = {}

    assert not await events_controller._event_owned_by_user(event, owner_id, cache)
    assert await events_controller._event_owned_by_user(event, owner_id, cache)


@pytest.mark.asyncio
async def test_file_progress_stream_delivers_only_owner_events(monkeypatch):
    owner_id = uuid4()
    other_id = uuid4()
    owner_file = uuid4()
    other_file = uuid4()

    class PubSub:
        def __init__(self):
            self.messages = [
                {
                    "type": "message",
                    "data": json.dumps({"type": "progress", "file_id": str(owner_file)}),
                },
                {
                    "type": "message",
                    "data": json.dumps({"type": "progress", "file_id": str(other_file)}),
                },
            ]
            self.subscribed = None
            self.closed = False

        async def subscribe(self, channel):
            self.subscribed = channel

        async def get_message(self, **kwargs):
            return self.messages.pop(0) if self.messages else None

        async def unsubscribe(self, channel):
            return None

        async def aclose(self):
            self.closed = True

    class Redis:
        def __init__(self, pubsub):
            self._pubsub = pubsub

        def pubsub(self):
            return self._pubsub

    class StreamRequest:
        cookies = {auth.SESSION_COOKIE_NAME: "session-token"}

        def __init__(self):
            self.checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 2

    owners = iter((owner_id, other_id))
    closed_sessions = 0

    class Session:
        async def execute(self, statement):
            value = next(owners)
            return SimpleNamespace(scalar_one_or_none=lambda: value)

    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            nonlocal closed_sessions
            closed_sessions += 1

    pubsub = PubSub()
    monkeypatch.setattr(
        events_controller, "get_redis_client", lambda: Redis(pubsub)
    )
    monkeypatch.setattr(events_controller, "async_session_factory", Factory())
    stream = events_controller.file_progress_stream(StreamRequest(), owner_id)

    connection = await anext(stream)
    owner_event = await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert "connection" in connection
    assert str(owner_file) in owner_event
    assert str(other_file) not in owner_event
    assert pubsub.subscribed == "events:file_progress:global"
    assert pubsub.closed
    assert closed_sessions == 2
