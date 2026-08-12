from types import SimpleNamespace

import pytest

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
    assert redis.values["active_agent_thread:thread-1"] == "missing"


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
