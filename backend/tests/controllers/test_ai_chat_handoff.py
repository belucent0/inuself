import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

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

        async def get_message(self, *_args, **_kwargs):
            return SimpleNamespace(
                message_id="answer", thread_id="thread", role="assistant"
            )

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

    class Queue:
        def enqueue_agent_job(self, **_kwargs):
            raise ConnectionError("broker down")

    session = Session()
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
    assert metadata_updates[0][0] == "answer"


@pytest.mark.asyncio
async def test_regeneration_keeps_old_answer_until_replacement_completes(monkeypatch):
    user_id = uuid4()
    old_user = SimpleNamespace(message_id="question", role="user")
    old_answer = SimpleNamespace(
        message_id="old-answer",
        role="assistant",
        status="completed",
        metadata={"replaces_message_ids": ["original-answer"]},
    )
    added_metadata = []

    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_last_message(self, *_args, role=None, **_kwargs):
            return old_user if role == "user" else old_answer

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


@pytest.mark.asyncio
async def test_stream_lookup_rejects_assistant_from_another_thread():
    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_message(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="other-thread", role="assistant")

    with pytest.raises(ai_chat_controller.HTTPException) as raised:
        await ai_chat_controller.stream_message_v2(
            thread_id="thread",
            message_id="answer",
            svc=Service(),
            user_id=uuid4(),
            session=SimpleNamespace(),
        )

    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_lookup_rejects_malformed_message_id_as_not_found():
    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_message(self, *_args, **_kwargs):
            raise ValueError("bad UUID")

    with pytest.raises(ai_chat_controller.HTTPException) as raised:
        await ai_chat_controller.stream_message_v2(
            thread_id="thread",
            message_id="not-a-uuid",
            svc=Service(),
            user_id=uuid4(),
            session=SimpleNamespace(),
        )

    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_regenerate_rejects_active_latest_assistant_before_mutation():
    added = False

    class Service:
        async def get_thread(self, *_args, **_kwargs):
            return SimpleNamespace(thread_id="thread")

        async def get_last_message(self, *_args, role=None, **_kwargs):
            if role == "user":
                return SimpleNamespace(message_id="question", role="user")
            return SimpleNamespace(
                message_id="answer",
                role="assistant",
                status="thinking",
                metadata={},
            )

        async def add_message(self, *_args, **_kwargs):
            nonlocal added
            added = True

    with pytest.raises(ai_chat_controller.HTTPException) as raised:
        await ai_chat_controller.regenerate_response(
            thread_id="thread",
            request=ai_chat_controller.RegenerateRequest(),
            settings=SimpleNamespace(),
            svc=Service(),
            user_id=uuid4(),
            session=SimpleNamespace(),
        )

    assert raised.value.status_code == 409
    assert added is False


@pytest.mark.asyncio
async def test_named_active_assistant_integrity_error_maps_to_409():
    class Session:
        rolled_back = False

        async def rollback(self):
            self.rolled_back = True

    original = SimpleNamespace(
        diag=SimpleNamespace(
            constraint_name="uq_ai_message_active_assistant_per_thread"
        )
    )
    session = Session()

    with pytest.raises(ai_chat_controller.HTTPException) as raised:
        await ai_chat_controller._raise_database_error(
            session,
            IntegrityError("insert", {}, original),
            "answer",
        )

    assert raised.value.status_code == 409
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_other_integrity_error_stays_generic_500():
    class Session:
        async def rollback(self):
            return None

    original = SimpleNamespace(
        diag=SimpleNamespace(constraint_name="some_other_constraint")
    )
    with pytest.raises(ai_chat_controller.HTTPException) as raised:
        await ai_chat_controller._raise_database_error(
            Session(), IntegrityError("insert", {}, original), "answer"
        )

    assert raised.value.status_code == 500
    assert "some_other_constraint" not in raised.value.detail


@pytest.mark.asyncio
async def test_legacy_failed_content_is_not_relayed(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Service:
        async def get_message(self, *_args):
            return SimpleNamespace(
                status="failed",
                content="raw internal database error",
                partial_content=None,
            )

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

    monkeypatch.setattr(ai_chat_controller, "async_session_factory", Session)
    monkeypatch.setattr(ai_chat_controller, "get_thread_service", lambda _session: Service())
    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())

    event = json.loads(
        (await anext(ai_chat_controller._relay_agent_events("answer"))).removeprefix(
            "data: "
        )
    )

    assert event["type"] == "error"
    assert event["data"] == ai_chat_controller.AGENT_FAILURE_CONTENT


@pytest.mark.asyncio
async def test_live_worker_error_payload_is_sanitized(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Service:
        async def get_message(self, *_args):
            return SimpleNamespace(
                status="generating",
                content="",
                partial_content=None,
            )

    class PubSub:
        async def subscribe(self, *_args):
            return None

        async def get_message(self, **_kwargs):
            return {
                "data": json.dumps(
                    {"type": "error", "data": "SECRET database exception"}
                )
            }

        async def unsubscribe(self, *_args):
            return None

        async def aclose(self):
            return None

    class Redis:
        def pubsub(self):
            return PubSub()

    monkeypatch.setattr(ai_chat_controller, "async_session_factory", Session)
    monkeypatch.setattr(
        ai_chat_controller, "get_thread_service", lambda _session: Service()
    )
    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())

    events = ai_chat_controller._relay_agent_events("answer")
    await anext(events)  # current status
    error_event = await anext(events)
    await events.aclose()

    assert ai_chat_controller.AGENT_FAILURE_CONTENT in error_event
    assert "SECRET" not in error_event
