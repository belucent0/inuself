import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.controllers import ai_chat_controller


@pytest.mark.asyncio
async def test_agent_stream_starts_with_accepted_message_ids(monkeypatch):
    accepted = {
        "thread_id": "thread",
        "message_id": "answer",
        "user_message_id": "question",
    }
    class PubSub:
        async def subscribe(self, *_args):
            return None

        async def unsubscribe(self, *_args):
            return None

        async def aclose(self):
            return None

    class Redis:
        def pubsub(self):
            return PubSub()

    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())
    response = ai_chat_controller._agent_stream_response("answer", accepted)

    first_event = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert response.media_type == "text/event-stream"
    assert json.loads(first_event.removeprefix("data: ")) == {
        "type": "accepted",
        "data": accepted,
    }
    assert ai_chat_controller.AddMessageRequest(query="hello").stream is False


@pytest.mark.asyncio
async def test_agent_stream_preserves_accepted_ids_when_relay_is_unavailable(
    monkeypatch,
):
    accepted = {
        "thread_id": "thread",
        "message_id": "answer",
        "user_message_id": "question",
    }

    class PubSub:
        async def subscribe(self, *_args):
            raise ConnectionError("redis unavailable")

        async def unsubscribe(self, *_args):
            return None

        async def aclose(self):
            return None

    class Redis:
        def pubsub(self):
            return PubSub()

    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())
    response = ai_chat_controller._agent_stream_response("answer", accepted)

    accepted_event = json.loads(
        (await anext(response.body_iterator)).removeprefix("data: ")
    )
    error_event = json.loads(
        (await anext(response.body_iterator)).removeprefix("data: ")
    )
    await response.body_iterator.aclose()

    assert accepted_event == {"type": "accepted", "data": accepted}
    assert error_event == {
        "type": "error",
        "data": {
            "code": "relay_unavailable",
            "message": "Streaming relay is temporarily unavailable",
            "retryable": True,
        },
    }


@pytest.mark.asyncio
async def test_reconnect_releases_lookup_transaction_before_streaming():
    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_messages(self, *_args, **_kwargs):
            return [SimpleNamespace(message_id="answer", role="assistant")]

    class Session:
        rollbacks = 0

        async def rollback(self):
            self.rollbacks += 1

    session = Session()
    response = await ai_chat_controller.stream_message_v2(
        thread_id="thread",
        message_id="answer",
        svc=Service(),
        user_id=uuid4(),
        session=session,
    )

    assert response.media_type == "text/event-stream"
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_committed_message_stays_queued_when_broker_is_unavailable(monkeypatch):
    metadata_updates = []

    class Session:
        commits = 0
        rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class Service:
        async def update_message_metadata(self, message_id, **metadata):
            metadata_updates.append((message_id, metadata))

    class Redis:
        cleared = False

        async def set(self, *_args, **_kwargs):
            return True

        async def eval(self, *_args, **_kwargs):
            self.cleared = True

    class Queue:
        def enqueue_agent_job(self, **_kwargs):
            raise ConnectionError("broker down")

    session = Session()
    redis = Redis()
    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: redis)
    monkeypatch.setattr(ai_chat_controller, "get_task_queue", lambda: Queue())

    await ai_chat_controller._commit_and_enqueue_agent(
        session=session,
        svc=Service(),
        thread_id="thread",
        user_id=uuid4(),
        user_message_id="question",
        assistant_message_id="answer",
    )

    assert session.commits == 1
    assert session.rollbacks == 0
    assert redis.cleared is False
    assert metadata_updates[0][0] == "answer"


@pytest.mark.asyncio
async def test_regeneration_keeps_old_answer_until_replacement_completes(monkeypatch):
    user_id = uuid4()
    old_user = SimpleNamespace(message_id="question", role="user")
    old_answer = SimpleNamespace(
        message_id="old-answer",
        role="assistant",
        metadata={"replaces_message_ids": ["original-answer"]},
    )
    added_metadata = []

    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_messages(self, *_args, **_kwargs):
            return [old_user, old_answer]

        async def add_message(self, *_args, **kwargs):
            added_metadata.append(kwargs["metadata"])
            return SimpleNamespace(message_id="new-answer")

    async def handoff(**_kwargs):
        return None

    monkeypatch.setattr(ai_chat_controller, "_commit_and_enqueue_agent", handoff)

    response = await ai_chat_controller.regenerate_response(
        thread_id="thread",
        request=ai_chat_controller.RegenerateRequest(mode="simple"),
        settings=SimpleNamespace(),
        svc=Service(),
        user_id=user_id,
        session=SimpleNamespace(),
    )

    assert response.media_type == "text/event-stream"
    assert added_metadata[0]["replaces_message_ids"] == [
        "old-answer",
        "original-answer",
    ]
