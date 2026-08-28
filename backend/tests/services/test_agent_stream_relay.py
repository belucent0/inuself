import json
from types import SimpleNamespace

import pytest

from app.controllers import ai_chat_controller


@pytest.mark.asyncio
async def test_snapshot_sequence_drops_already_persisted_events(monkeypatch):
    events = [
        {
            "data": json.dumps(
                {"type": "token", "data": "duplicate", "content_sequence": 2}
            )
        },
        {
            "data": json.dumps(
                {
                    "type": "content",
                    "data": "stale-content",
                    "content_sequence": 2,
                }
            )
        },
        {
            "data": json.dumps(
                {"type": "token", "data": "C", "content_sequence": 3}
            )
        },
        {
            "data": json.dumps(
                {"type": "content", "data": "ABC", "content_sequence": 3}
            )
        },
        {"data": json.dumps({"type": "done", "data": {"content": "ABC"}})},
    ]

    class PubSub:
        async def subscribe(self, *_args):
            return None

        async def get_message(self, **_kwargs):
            return events.pop(0)

        async def unsubscribe(self, *_args):
            return None

        async def aclose(self):
            return None

    class Redis:
        def pubsub(self):
            return PubSub()

    class SessionFactory:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    message = SimpleNamespace(
        status="generating",
        partial_content="AB",
        content="",
        metadata={"_content_sequence": 2},
    )

    class Service:
        async def get_message(self, _message_id):
            return message

    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())
    monkeypatch.setattr(ai_chat_controller, "async_session_factory", SessionFactory)
    monkeypatch.setattr(ai_chat_controller, "get_thread_service", lambda _session: Service())

    output = "".join(
        [event async for event in ai_chat_controller._relay_agent_events("message")]
    )

    assert "partial_restore" in output
    assert "duplicate" not in output
    assert "stale-content" not in output
    assert '"data": "C"' in output
    assert '"type": "content", "data": "ABC"' in output
    assert '"type": "done"' in output


@pytest.mark.asyncio
async def test_polling_restore_advances_sequence_before_queued_token(monkeypatch):
    events = [
        None,
        {
            "data": json.dumps(
                {"type": "token", "data": "duplicate", "content_sequence": 2}
            )
        },
        {"data": json.dumps({"type": "done", "data": {"content": "AB"}})},
    ]

    class PubSub:
        async def subscribe(self, *_args):
            return None

        async def get_message(self, **_kwargs):
            return events.pop(0)

        async def unsubscribe(self, *_args):
            return None

        async def aclose(self):
            return None

    class Redis:
        def pubsub(self):
            return PubSub()

    class SessionFactory:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    snapshots = [
        SimpleNamespace(
            status="generating",
            partial_content="A",
            content="",
            metadata={"_content_sequence": 1},
        ),
        SimpleNamespace(
            status="generating",
            partial_content="AB",
            content="",
            metadata={"_content_sequence": 2},
        ),
    ]

    class Service:
        async def get_message(self, _message_id):
            return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    monkeypatch.setattr(ai_chat_controller, "get_redis_client", lambda: Redis())
    monkeypatch.setattr(ai_chat_controller, "async_session_factory", SessionFactory)
    monkeypatch.setattr(ai_chat_controller, "get_thread_service", lambda _session: Service())

    output = "".join(
        [event async for event in ai_chat_controller._relay_agent_events("message")]
    )

    assert output.count("partial_restore") == 2
    assert "duplicate" not in output
    assert '"type": "done"' in output
