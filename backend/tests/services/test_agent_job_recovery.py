from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.services import agent_job_recovery
from app.utils.task_queue_adapter import (
    ACTIVE_JOB_TTL,
    AGENT_DISPATCH_KEY_PREFIX,
    CeleryAdapter,
)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Redis:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def exists(self, key):
        return key in self.values

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_recovery_requeues_only_messages_without_dispatch_marker(monkeypatch):
    missing = SimpleNamespace(
        id="missing",
        thread_id="thread-1",
        metadata_={"agent_user_id": "user", "agent_user_message_id": "question"},
    )
    dispatched = SimpleNamespace(
        id="dispatched",
        thread_id="thread-2",
        metadata_={"agent_user_id": "user", "agent_user_message_id": "question-2"},
    )

    class Repository:
        def __init__(self, _session):
            pass

        async def get_stale_active_assistant_messages(self, _cutoff):
            return []

        async def get_stale_queued_assistant_messages(self, _cutoff):
            return [missing, dispatched]

    redis = _Redis({f"{AGENT_DISPATCH_KEY_PREFIX}dispatched": "task-id"})
    jobs = []
    queue = SimpleNamespace(enqueue_agent_job=lambda **kwargs: jobs.append(kwargs))
    monkeypatch.setattr(agent_job_recovery, "async_session_factory", _Session)
    monkeypatch.setattr(agent_job_recovery, "ThreadRepository", Repository)
    monkeypatch.setattr(agent_job_recovery, "get_redis_client", lambda: redis)
    monkeypatch.setattr(agent_job_recovery, "get_task_queue", lambda: queue)

    recovered = await agent_job_recovery.recover_stale_agent_jobs()

    assert recovered == 1
    assert [job["assistant_message_id"] for job in jobs] == ["missing"]


def test_agent_enqueue_records_dispatch_marker():
    marker = []
    adapter = CeleryAdapter.__new__(CeleryAdapter)
    adapter.celery = SimpleNamespace(
        send_task=lambda *_args, **_kwargs: SimpleNamespace(id="task-id")
    )
    adapter.redis = SimpleNamespace(
        setex=lambda key, ttl, value: marker.append((key, ttl, value))
    )

    result = adapter.enqueue_agent_job(
        thread_id="thread",
        user_id="user",
        user_message_id="question",
        assistant_message_id="answer",
    )

    assert result == "task-id"
    assert marker == [
        (f"{AGENT_DISPATCH_KEY_PREFIX}answer", ACTIVE_JOB_TTL, "task-id")
    ]


@pytest.mark.asyncio
async def test_stale_finalizer_uses_worker_message_lock_and_conditional_update(
    monkeypatch,
):
    message_id = uuid4()
    calls = []

    class Session(_Session):
        async def commit(self):
            calls.append("commit")

    class Repository:
        def __init__(self, _session):
            pass

        async def get_stale_active_assistant_messages(self, _cutoff):
            return [SimpleNamespace(id=message_id)]

        async def fail_stale_active_assistant_message(
            self, received_id, _cutoff, content
        ):
            calls.append(("update", received_id, content))
            return True

    class Lock:
        async def acquire(self, *, blocking):
            calls.append(("acquire", blocking))
            return True

        async def release(self):
            calls.append("release")

    class Redis:
        def lock(self, key, **kwargs):
            calls.append(("lock", key, kwargs))
            return Lock()

    monkeypatch.setattr(agent_job_recovery, "async_session_factory", Session)
    monkeypatch.setattr(agent_job_recovery, "ThreadRepository", Repository)
    monkeypatch.setattr(agent_job_recovery, "get_redis_client", lambda: Redis())

    finalized = await agent_job_recovery.finalize_stale_active_agent_messages()

    assert finalized == 1
    assert calls[0][1].endswith(str(message_id))
    assert calls[1] == ("acquire", False)
    assert calls[-2:] == ["commit", "release"]


@pytest.mark.asyncio
async def test_stale_finalizer_fails_closed_when_redis_is_unavailable(monkeypatch):
    message_id = uuid4()
    updated = False

    class Repository:
        def __init__(self, _session):
            pass

        async def get_stale_active_assistant_messages(self, _cutoff):
            return [SimpleNamespace(id=message_id)]

        async def fail_stale_active_assistant_message(self, *_args):
            nonlocal updated
            updated = True

    class Redis:
        def lock(self, *_args, **_kwargs):
            raise RedisError("unavailable")

    monkeypatch.setattr(agent_job_recovery, "async_session_factory", _Session)
    monkeypatch.setattr(agent_job_recovery, "ThreadRepository", Repository)
    monkeypatch.setattr(agent_job_recovery, "get_redis_client", lambda: Redis())

    assert await agent_job_recovery.finalize_stale_active_agent_messages() == 0
    assert updated is False
