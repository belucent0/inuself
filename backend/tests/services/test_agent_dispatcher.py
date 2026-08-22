from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.controllers import ai_chat_controller
from app.services import agent_dispatcher
from app.utils.task_queue_adapter import (
    ACTIVE_JOB_TTL,
    AGENT_DISPATCH_KEY_PREFIX,
    CeleryAdapter,
)


@pytest.mark.asyncio
async def test_publish_ambiguity_keeps_committed_message_queued(monkeypatch):
    calls: list[str] = []

    class Redis:
        async def set(self, *_args, **_kwargs):
            return True

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

    async def clear_slot(*_args, **_kwargs):
        calls.append("clear")

    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())
    monkeypatch.setattr(ai_chat_controller, "dispatch_agent_job", ambiguous_publish)
    monkeypatch.setattr(ai_chat_controller, "_clear_agent_thread_slot", clear_slot)

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


def test_agent_enqueue_records_worker_visible_dispatch_marker():
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

    monkeypatch.setattr(agent_dispatcher, "async_session_factory", Session)
    monkeypatch.setattr(agent_dispatcher, "dispatch_agent_job", dispatch)

    assert await agent_dispatcher.reconcile_agent_jobs_once() == 1
