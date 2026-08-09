from uuid import uuid4

import pytest

from app.controllers import ai_chat_controller


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
