import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import summary_runner


class _Lock:
    def __init__(self, redis):
        self.redis = redis

    async def acquire(self, **_kwargs):
        if self.redis.held:
            return False
        self.redis.held = True
        return True

    async def release(self):
        self.redis.held = False


class _Redis:
    def __init__(self):
        self.held = False
        self.keys = []

    def lock(self, key, **_kwargs):
        self.keys.append(key)
        return _Lock(self)


@pytest.mark.asyncio
async def test_full_and_partial_summary_share_write_lock(monkeypatch):
    redis = _Redis()
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def summarize(*_args):
        entered.set()
        await finish.wait()
        return "title", "summary", True

    regenerate = AsyncMock()
    monkeypatch.setattr(summary_runner, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        summary_runner, "_summarize_with_block_generator_unlocked", summarize
    )
    monkeypatch.setattr(summary_runner, "_regenerate_block_unlocked", regenerate)

    full = asyncio.create_task(
        summary_runner.summarize_with_block_generator("file-1", "text")
    )
    await entered.wait()

    with pytest.raises(ValueError, match="already in progress"):
        await summary_runner.regenerate_block("file-1", "title", "text")

    regenerate.assert_not_awaited()
    finish.set()
    assert await full == ("title", "summary", True)
    assert redis.keys == ["lock:summary:write:file-1"] * 2
    assert not redis.held
