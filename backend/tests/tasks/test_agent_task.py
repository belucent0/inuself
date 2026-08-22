from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.tasks import agent_task


class _Lock:
    async def acquire(self, **_kwargs):
        return True

    async def release(self):
        return None


class _Redis:
    def lock(self, *_args, **_kwargs):
        return _Lock()

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_load_run_preserves_controller_routing_context(monkeypatch):
    thread_id = uuid4()
    user_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    thread = SimpleNamespace(id=thread_id)
    user_message = SimpleNamespace(
        thread_id=thread_id,
        role="user",
        content="question",
    )
    assistant_message = SimpleNamespace(
        thread_id=thread_id,
        role="assistant",
        status="queued",
        metadata_={
            "mode": "reasoning",
            "context": {"reasoning": "high", "allow_remote": True},
        },
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Repository:
        async def get_thread_by_user(self, *_args, **_kwargs):
            return thread

        async def get_message(self, message_id):
            return user_message if message_id == user_message_id else assistant_message

    monkeypatch.setattr(agent_task, "async_session_factory", Session)
    monkeypatch.setattr(agent_task, "ThreadRepository", lambda _session: Repository())

    loaded = await agent_task._load_run(
        thread_id=str(thread_id),
        user_id=str(user_id),
        user_message_id=str(user_message_id),
        assistant_message_id=str(assistant_message_id),
    )

    assert loaded is not None
    query, mode, context, _base_metadata = loaded
    assert query == "question"
    assert mode == "reasoning"
    assert context["reasoning"] == "high"
    assert context["allow_remote"] is True


@pytest.mark.asyncio
async def test_thread_slot_is_released_before_done_event(monkeypatch):
    calls: list[str] = []
    streamed = {}
    redis = _Redis()

    async def load_run(**_kwargs):
        context = {"reasoning": "high", "allow_remote": True}
        return "question", "reasoning", context, {"context": context}

    async def noop(*_args, **_kwargs):
        return None

    async def publish(_redis, _message_id, event_type, _data, **_kwargs):
        calls.append(event_type)

    async def clear_slot(_redis, _thread_id, _message_id):
        calls.append("clear_slot")

    async def stream(**kwargs):
        streamed.update(kwargs)
        yield {"type": "token", "data": "answer"}
        yield {"type": "done", "data": None}

    monkeypatch.setattr(agent_task, "get_settings", lambda: SimpleNamespace(redis_url="redis://test"))
    monkeypatch.setattr(agent_task.Redis, "from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr(agent_task, "_load_run", load_run)
    monkeypatch.setattr(agent_task, "_save_status", noop)
    monkeypatch.setattr(agent_task, "_save_completed", noop)
    monkeypatch.setattr(agent_task, "_publish", publish)
    monkeypatch.setattr(agent_task, "_clear_thread_slot", clear_slot)
    monkeypatch.setattr(agent_task, "stream_ai_agent", stream)

    result = await agent_task.run_agent_message(
        thread_id="thread",
        user_id="user",
        user_message_id="user-message",
        assistant_message_id="assistant-message",
    )

    assert result["status"] == "completed"
    assert calls.index("clear_slot") < calls.index("done")
    assert streamed["metadata"]["reasoning"] == "high"
    assert streamed["metadata"]["allow_remote"] is True


@pytest.mark.asyncio
async def test_agent_failure_hides_internal_exception_from_user(monkeypatch):
    saved: list[tuple[str, str | None]] = []
    published: list[tuple[str, object]] = []
    redis = _Redis()

    async def load_run(**_kwargs):
        return "question", "auto", {}, {}

    async def save_status(_message_id, status, *, content=None, **_kwargs):
        saved.append((status, content))

    async def publish(_redis, _message_id, event_type, data, **_kwargs):
        published.append((event_type, data))

    async def stream(**_kwargs):
        raise RuntimeError("postgres://secret@internal/provider")
        yield

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_task, "get_settings", lambda: SimpleNamespace(redis_url="redis://test"))
    monkeypatch.setattr(agent_task.Redis, "from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr(agent_task, "_load_run", load_run)
    monkeypatch.setattr(agent_task, "_save_status", save_status)
    monkeypatch.setattr(agent_task, "_publish", publish)
    monkeypatch.setattr(agent_task, "_clear_thread_slot", noop)
    monkeypatch.setattr(agent_task, "stream_ai_agent", stream)

    with pytest.raises(RuntimeError, match="secret"):
        await agent_task.run_agent_message(
            thread_id="thread",
            user_id="user",
            user_message_id="user-message",
            assistant_message_id="assistant-message",
        )

    assert saved[-1] == ("failed", "Agent execution failed. Please retry.")
    assert published[-1] == (
        "error",
        "Agent execution failed. Please retry.",
    )
