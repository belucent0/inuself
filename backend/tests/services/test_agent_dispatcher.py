from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.controllers import ai_chat_controller
from app.services import agent_dispatcher
from app.utils.task_queue_adapter import (
    AGENT_DISPATCH_KEY_PREFIX,
    CeleryAdapter,
)


@pytest.mark.asyncio
async def test_publish_ambiguity_keeps_committed_message_queued(monkeypatch):
    calls: list[str] = []

    class Session:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class Service:
        async def update_message_metadata(self, *_args, **_kwargs):
            calls.append("outbox")
            return object()

    async def ambiguous_publish(*_args, **_kwargs):
        calls.append("publish")
        raise OSError("broker confirmation lost")

    monkeypatch.setattr(ai_chat_controller, "dispatch_agent_job", ambiguous_publish)

    await ai_chat_controller._commit_and_enqueue_agent(
        session=Session(),
        svc=Service(),
        thread_id=str(uuid4()),
        user_id=uuid4(),
        user_message_id=str(uuid4()),
        assistant_message_id=str(uuid4()),
    )

    assert calls == ["outbox", "commit", "publish"]


@pytest.mark.asyncio
async def test_dispatch_claim_prevents_duplicate_publish(monkeypatch):
    class Redis:
        async def set(self, *_args, **_kwargs):
            return False

    monkeypatch.setattr(agent_dispatcher, "get_redis_client", lambda: Redis())
    monkeypatch.setattr(
        agent_dispatcher,
        "get_task_queue",
        lambda: (_ for _ in ()).throw(AssertionError("must not publish")),
    )

    dispatched = await agent_dispatcher.dispatch_agent_job(
        "assistant-message",
        {
            "thread_id": "thread",
            "user_id": "user",
            "user_message_id": "user-message",
        },
    )

    assert dispatched is False


@pytest.mark.asyncio
async def test_successful_publish_extends_dispatch_claim(monkeypatch):
    calls: list[tuple] = []

    class Redis:
        async def set(self, *args, **kwargs):
            calls.append(("set", args, kwargs))
            return True

        async def eval(self, *args):
            calls.append(("eval", args))
            return 1

    class Queue:
        def enqueue_agent_job(self, **kwargs):
            calls.append(("publish", kwargs))

    monkeypatch.setattr(agent_dispatcher, "get_redis_client", lambda: Redis())
    monkeypatch.setattr(agent_dispatcher, "get_task_queue", lambda: Queue())

    dispatched = await agent_dispatcher.dispatch_agent_job(
        "assistant-message",
        {
            "thread_id": "thread",
            "user_id": "user",
            "user_message_id": "user-message",
        },
    )

    assert dispatched is True
    assert [call[0] for call in calls] == ["set", "publish", "eval"]
    assert calls[0][1][0] == f"{AGENT_DISPATCH_KEY_PREFIX}assistant-message"
    assert calls[-1][1][-1] == agent_dispatcher.ACTIVE_JOB_TTL


def test_agent_enqueue_does_not_write_dispatch_marker():
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
    assert marker == []


@pytest.mark.asyncio
async def test_fast_worker_delete_is_not_revived_by_adapter_marker(monkeypatch):
    marker_key = f"{AGENT_DISPATCH_KEY_PREFIX}answer"
    values = {}
    publish_count = 0

    class AsyncRedis:
        async def set(self, key, value, *, nx=False, ex=None):
            if nx and key in values:
                return False
            values[key] = value
            return True

        async def eval(self, _script, _keys, key, claim_id, _ttl):
            return int(values.get(key) == claim_id)

    class SyncRedis:
        def setex(self, key, _ttl, value):
            values[key] = value

    class Celery:
        def send_task(self, *_args, **_kwargs):
            nonlocal publish_count
            publish_count += 1
            # The worker acquires its message lock and removes the dispatch claim
            # before the producer's send_task call returns.
            values.pop(marker_key, None)
            return SimpleNamespace(id="answer")

    adapter = CeleryAdapter.__new__(CeleryAdapter)
    adapter.celery = Celery()
    adapter.redis = SyncRedis()
    monkeypatch.setattr(agent_dispatcher, "get_redis_client", AsyncRedis)
    monkeypatch.setattr(agent_dispatcher, "get_task_queue", lambda: adapter)

    job = {
        "thread_id": "thread",
        "user_id": "user",
        "user_message_id": "question",
    }
    assert await agent_dispatcher.dispatch_agent_job("answer", job) is True
    assert marker_key not in values

    # A queued row left by an early worker crash is immediately dispatchable again.
    assert await agent_dispatcher.dispatch_agent_job("answer", job) is True
    assert marker_key not in values
    assert publish_count == 2


@pytest.mark.asyncio
async def test_reconcile_scans_beyond_first_hundred_marker_held_jobs(monkeypatch):
    messages = [
        SimpleNamespace(
            id=f"message-{index}",
            metadata_={
                "_agent_job": {
                    "thread_id": "thread",
                    "user_id": "user",
                    "user_message_id": "question",
                }
            },
        )
        for index in range(101)
    ]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            limit = getattr(statement._limit_clause, "value", None)
            selected = messages if limit is None else messages[:limit]
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: selected)
            )

    async def dispatch(message_id, _job):
        return message_id == "message-100"

    async def finalize():
        return 0

    monkeypatch.setattr(agent_dispatcher, "async_session_factory", Session)
    monkeypatch.setattr(agent_dispatcher, "dispatch_agent_job", dispatch)
    monkeypatch.setattr(
        agent_dispatcher, "finalize_stale_active_agent_messages", finalize
    )

    assert await agent_dispatcher.reconcile_agent_jobs_once() == 1


@pytest.mark.asyncio
async def test_stale_finalizer_uses_worker_lock_and_conditional_update(monkeypatch):
    message_id = uuid4()
    calls = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

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

    monkeypatch.setattr(agent_dispatcher, "async_session_factory", Session)
    monkeypatch.setattr(agent_dispatcher, "ThreadRepository", Repository)
    monkeypatch.setattr(agent_dispatcher, "get_redis_client", lambda: Redis())

    finalized = await agent_dispatcher.finalize_stale_active_agent_messages()

    assert finalized == 1
    assert calls[0][1].endswith(str(message_id))
    assert calls[1] == ("acquire", False)
    assert calls[-2:] == ["commit", "release"]


@pytest.mark.asyncio
async def test_stale_finalizer_fails_closed_when_redis_is_unavailable(monkeypatch):
    message_id = uuid4()
    updated = False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

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

    monkeypatch.setattr(agent_dispatcher, "async_session_factory", Session)
    monkeypatch.setattr(agent_dispatcher, "ThreadRepository", Repository)
    monkeypatch.setattr(agent_dispatcher, "get_redis_client", lambda: Redis())

    assert await agent_dispatcher.finalize_stale_active_agent_messages() == 0
    assert updated is False
