from types import SimpleNamespace

import pytest

from app.services import translation_runner


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Repository:
    def __init__(self, _session):
        pass

    async def get_by_file_id(self, _file_id):
        return SimpleNamespace(transcription={"segments": [{"text": "hello"}]})


@pytest.mark.asyncio
async def test_translation_finalized_uses_chunks_field_contract(monkeypatch):
    published = []

    async def translate_chunk(**kwargs):
        kwargs["state"].status = "success"

    async def persist(*_args):
        return None

    monkeypatch.setattr(translation_runner, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(translation_runner, "TranscriptionRepository", _Repository)
    monkeypatch.setattr(translation_runner, "_translate_one_chunk", translate_chunk)
    monkeypatch.setattr(translation_runner, "_persist_progress_only", persist)
    monkeypatch.setattr(translation_runner, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        translation_runner,
        "publish_file_progress",
        lambda **kwargs: published.append(kwargs),
    )

    result = await translation_runner.translate_transcription("file-1")

    assert result["success"] is True
    assert published[-1]["metadata"] == {
        "event_subtype": "translation_finalized",
        "target_lang": "ko",
        "success": True,
        "chunks_done": 1,
        "chunks_total": 1,
        "chunks_failed": 0,
    }
