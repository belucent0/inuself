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
async def test_thread_slot_is_released_before_done_event(monkeypatch):
    calls: list[str] = []
    redis = _Redis()

    async def load_run(**_kwargs):
        return "question", "simple", {}, {}

    async def noop(*_args, **_kwargs):
        return None

    async def publish(_redis, _message_id, event_type, _data):
        calls.append(event_type)

    async def clear_slot(_redis, _thread_id, _message_id):
        calls.append("clear_slot")

    async def stream(**_kwargs):
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

    class Service:
        async def update_message_status(self, *_args, **_kwargs):
            return SimpleNamespace()

        async def update_message_partial_content(self, *_args, **_kwargs):
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

        async def delete_message(self, message):
            deleted.append(message.id)

    monkeypatch.setattr(agent_task, "async_session_factory", Session)
    monkeypatch.setattr(agent_task, "get_thread_service", lambda _session: Service())
    monkeypatch.setattr(agent_task, "ThreadRepository", Repository)

    await agent_task._save_completed(
        str(new_message_id),
        "new answer",
        {"replaces_message_ids": [str(old_message_id)]},
    )

    assert deleted == [old_message_id]
