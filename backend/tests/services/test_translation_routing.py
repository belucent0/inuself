from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import translation_runner as runner


@pytest.mark.asyncio
async def test_translation_chunk_uses_gateway_simple_tier(monkeypatch):
    captured = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return '{"translations":["hello-ko"]}'

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def commit(self):
            return None

    repository = AsyncMock()
    monkeypatch.setattr(runner, "request_ai_gateway_completion_async", fake_completion)
    monkeypatch.setattr(runner, "AsyncSessionLocal", Session)
    monkeypatch.setattr(runner, "TranscriptionRepository", lambda _: repository)
    monkeypatch.setattr(runner, "_publish_chunk_event", AsyncMock())

    state = runner.ChunkState(chunk_idx=0, segment_indices=[0])
    segments = [{"text": "Hello"}]
    await runner._translate_one_chunk(
        state=state,
        segments=segments,
        settings=object(),
        file_id=uuid4(),
        target_lang="ko",
        chunk_states=[state],
        transcription_data={"segments": segments},
    )

    assert captured["model"] == "tier-simple"
    assert "base_url" not in captured
    assert state.status == "success"
    assert segments[0]["translation_ko"] == "hello-ko"
