from types import SimpleNamespace

import pytest

from app.tasks import agent_task


class _Lock:
    async def acquire(self, **_kwargs):
        return True

    async def release(self):
        return None


class _Redis:
    def lock(self, *_args, **_kwargs):
        return _Lock()

    async def aclose(self):
        return None


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
