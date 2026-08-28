from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.tasks import agent_task


class _Lock:
    async def acquire(self, **_kwargs):
        return True

    async def release(self):
        return None


class _Redis:
    def lock(self, *_args, **_kwargs):
        return _Lock()

    async def delete(self, *_args):
        return None

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_lock_acquire_error_is_retryable_and_closes_redis(monkeypatch):
    calls = []

    class FailingLock:
        async def acquire(self, **_kwargs):
            raise RedisError("unavailable")

        async def release(self):
            calls.append("release")

    class FailingRedis:
        def lock(self, *_args, **_kwargs):
            return FailingLock()

        async def aclose(self):
            calls.append("close")

    redis = FailingRedis()
    monkeypatch.setattr(
        agent_task, "get_settings", lambda: SimpleNamespace(redis_url="redis://test")
    )
    monkeypatch.setattr(agent_task.Redis, "from_url", lambda *_args, **_kwargs: redis)

    with pytest.raises(agent_task.AgentMessageUnavailable):
        await agent_task.run_agent_message(
            thread_id="thread",
            user_id="user",
            user_message_id="user-message",
            assistant_message_id="assistant-message",
        )

    assert calls == ["release", "close"]


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
async def test_done_event_is_published_after_conditional_completion(monkeypatch):
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
    monkeypatch.setattr(agent_task, "stream_ai_agent", stream)

    result = await agent_task.run_agent_message(
        thread_id="thread",
        user_id="user",
        user_message_id="user-message",
        assistant_message_id="assistant-message",
    )

    assert result["status"] == "completed"
    assert calls[-1] == "done"
    assert streamed["metadata"]["reasoning"] == "high"
    assert streamed["metadata"]["allow_remote"] is True


@pytest.mark.asyncio
async def test_partial_save_preserves_outbox_metadata_and_content_sequence(
    monkeypatch,
):
    message_id = uuid4()
    updates = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

    class Repository:
        def __init__(self, _session):
            pass

        async def get_message(self, received_id):
            assert received_id == message_id
            return SimpleNamespace(
                metadata_={"_agent_job": {"thread_id": "thread"}, "mode": "reasoning"}
            )

        async def update_active_assistant_message(self, received_id, **values):
            updates.append((received_id, values))
            return True

    monkeypatch.setattr(agent_task, "async_session_factory", Session)
    monkeypatch.setattr(agent_task, "ThreadRepository", Repository)

    await agent_task._save_partial(str(message_id), "partial", 7)

    assert updates == [
        (
            message_id,
            {
                "partial_content": "partial",
                "status": "generating",
                "metadata_": {
                    "_agent_job": {"thread_id": "thread"},
                    "mode": "reasoning",
                    "_content_sequence": 7,
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_completed_regeneration_deletes_replaced_answer(monkeypatch):
    thread_id = uuid4()
    old_message_id = uuid4()
    new_message_id = uuid4()
    deleted = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

    class Repository:
        def __init__(self, _session):
            pass

        async def get_message(self, message_id):
            if message_id == new_message_id:
                return SimpleNamespace(
                    id=new_message_id,
                    thread_id=thread_id,
                    role="assistant",
                )
            assert message_id == old_message_id
            return SimpleNamespace(
                id=old_message_id,
                thread_id=thread_id,
                role="assistant",
            )

        async def update_active_assistant_message(self, *_args, **_kwargs):
            return True

        async def delete_message(self, message):
            deleted.append(message.id)

    monkeypatch.setattr(agent_task, "async_session_factory", Session)
    monkeypatch.setattr(agent_task, "ThreadRepository", Repository)

    await agent_task._save_completed(
        str(new_message_id),
        "new answer",
        {"replaces_message_ids": [str(old_message_id)]},
    )

    assert deleted == [old_message_id]


@pytest.mark.asyncio
async def test_conditional_status_write_does_not_resurrect_terminal_message(monkeypatch):
    message_id = uuid4()

    class Session:
        committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            self.committed = True

    class Repository:
        def __init__(self, _session):
            pass

        async def update_active_assistant_message(self, *_args, **_kwargs):
            return False

    monkeypatch.setattr(agent_task, "async_session_factory", Session)
    monkeypatch.setattr(agent_task, "ThreadRepository", Repository)

    with pytest.raises(agent_task.AgentMessageTerminal):
        await agent_task._save_status(str(message_id), "generating")


@pytest.mark.asyncio
async def test_lost_lock_never_marks_replacement_run_failed(monkeypatch):
    saved_statuses: list[str] = []

    async def load_run(**_kwargs):
        return "question", "simple", {}, {}

    async def save_status(_message_id, status, **_kwargs):
        saved_statuses.append(status)

    async def lose_lock(_lock, lost):
        lost.set()

    async def publish(*_args, **_kwargs):
        return None

    async def stream(**_kwargs):
        await agent_task.asyncio.sleep(0)
        yield {"type": "token", "data": "stale"}

    monkeypatch.setattr(
        agent_task, "get_settings", lambda: SimpleNamespace(redis_url="redis://test")
    )
    monkeypatch.setattr(agent_task.Redis, "from_url", lambda *_args, **_kwargs: _Redis())
    monkeypatch.setattr(agent_task, "_load_run", load_run)
    monkeypatch.setattr(agent_task, "_save_status", save_status)
    monkeypatch.setattr(agent_task, "_publish", publish)
    monkeypatch.setattr(agent_task, "_refresh_lock", lose_lock)
    monkeypatch.setattr(agent_task, "stream_ai_agent", stream)

    with pytest.raises(agent_task.AgentMessageLockLost):
        await agent_task.run_agent_message(
            thread_id="thread",
            user_id="user",
            user_message_id="user-message",
            assistant_message_id="assistant-message",
        )

    assert saved_statuses == ["analyzing"]


def test_duplicate_delivery_gets_one_retry_after_lock_ttl(monkeypatch):
    retry_calls = []

    def busy(coro):
        coro.close()
        raise agent_task.AgentMessageBusy("assistant-message")

    def retry(**kwargs):
        retry_calls.append(kwargs)
        raise RuntimeError("retry scheduled")

    monkeypatch.setattr(agent_task, "_run_async", busy)
    monkeypatch.setattr(agent_task.process_agent_message, "retry", retry)
    monkeypatch.setattr(
        agent_task.process_agent_message.request, "retries", 7, raising=False
    )

    with pytest.raises(RuntimeError, match="retry scheduled"):
        agent_task.process_agent_message.run(
            thread_id="thread",
            user_id="user",
            user_message_id="user-message",
            assistant_message_id="assistant-message",
        )

    assert retry_calls[0]["countdown"] == agent_task.MESSAGE_LOCK_SECONDS + 1
    assert retry_calls[0]["max_retries"] == 8
    assert retry_calls[0]["kwargs"]["_lock_retry_count"] == 1


def test_lock_retry_is_bounded_independently_of_infra_retries(monkeypatch):
    def busy(coro):
        coro.close()
        raise agent_task.AgentMessageBusy("assistant-message")

    monkeypatch.setattr(agent_task, "_run_async", busy)

    result = agent_task.process_agent_message.run(
        thread_id="thread",
        user_id="user",
        user_message_id="user-message",
        assistant_message_id="assistant-message",
        _lock_retry_count=1,
    )

    assert result == {
        "status": "lock_unresolved",
        "message_id": "assistant-message",
    }


@pytest.mark.asyncio
async def test_agent_failure_hides_internal_exception_from_user(monkeypatch):
    saved: list[tuple[str, str | None]] = []
    published: list[tuple[str, object]] = []

    async def load_run(**_kwargs):
        return "question", "auto", {}, {}

    async def save_status(_message_id, status, *, content=None, **_kwargs):
        saved.append((status, content))

    async def publish(_redis, _message_id, event_type, data, **_kwargs):
        published.append((event_type, data))

    async def stream(**_kwargs):
        raise RuntimeError("postgres://secret@internal/provider")
        yield

    monkeypatch.setattr(
        agent_task,
        "get_settings",
        lambda: SimpleNamespace(redis_url="redis://test"),
    )
    monkeypatch.setattr(
        agent_task.Redis,
        "from_url",
        lambda *_args, **_kwargs: _Redis(),
    )
    monkeypatch.setattr(agent_task, "_load_run", load_run)
    monkeypatch.setattr(agent_task, "_save_status", save_status)
    monkeypatch.setattr(agent_task, "_publish", publish)
    monkeypatch.setattr(agent_task, "stream_ai_agent", stream)

    with pytest.raises(RuntimeError, match="secret"):
        await agent_task.run_agent_message(
            thread_id="thread",
            user_id="user",
            user_message_id="user-message",
            assistant_message_id="assistant-message",
        )

    assert saved[-1] == ("failed", agent_task.AGENT_FAILURE_CONTENT)
    assert published[-1] == ("error", agent_task.AGENT_FAILURE_CONTENT)
