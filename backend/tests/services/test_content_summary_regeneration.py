from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import ContentType, FileStatus
from app.services import summary_runner
from app.services.content_service import ContentService
import app.repositories.document_repository as document_repository
import app.repositories.file_repository as file_repository
import app.repositories.transcription_repository as transcription_repository


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FileRepository:
    def __init__(self, acquired, status=FileStatus.COMPLETED):
        content = SimpleNamespace(
            status=status,
            summary_sections={"blocks": []},
            user_id="user-1",
        )
        self.file = SimpleNamespace(content=content, content_type=ContentType.AUDIO)
        self.acquired = acquired
        self.acquire_statuses = []
        self.restored = []

    async def get_file(self, _file_id):
        return self.file

    async def try_acquire_summarizing_lock(self, _file_id, allowed_statuses=None):
        self.acquire_statuses.append(allowed_statuses)
        return self.acquired

    async def restore_summarizing_status(self, file_id, status):
        self.restored.append((file_id, status))
        return True

    async def add_llm_log(self, **_kwargs):
        return None


class _TranscriptionRepository:
    def __init__(self, _session):
        pass

    async def get_by_file_id(self, _file_id):
        return SimpleNamespace(transcription={"text": "source text"})


def _service(monkeypatch, acquired, status=FileStatus.COMPLETED):
    session = _Session()
    repo = _FileRepository(acquired, status)
    service = ContentService.__new__(ContentService)
    service.session = session
    monkeypatch.setattr(file_repository, "FileRepository", lambda _session: repo)
    monkeypatch.setattr(transcription_repository, "TranscriptionRepository", _TranscriptionRepository)
    monkeypatch.setattr(document_repository, "DocumentRepository", lambda _session: object())
    return service, session, repo


@pytest.mark.asyncio
async def test_partial_regeneration_rejects_shared_summary_lock(monkeypatch):
    service, _session, repo = _service(monkeypatch, acquired=False)
    regenerate = AsyncMock()
    monkeypatch.setattr(summary_runner, "regenerate_block", regenerate)

    with pytest.raises(ValueError, match="another summarization is in progress"):
        await service.regenerate_summary_block("file-1", "title", user_id="user-1")

    regenerate.assert_not_awaited()
    assert repo.acquire_statuses == [(FileStatus.COMPLETED,)]
    assert repo.restored == []


@pytest.mark.asyncio
async def test_partial_regeneration_rejects_queued_automatic_summary(monkeypatch):
    service, _session, repo = _service(
        monkeypatch, acquired=True, status=FileStatus.SUMMARY_QUEUED
    )

    with pytest.raises(ValueError, match="unless summarization is stable"):
        await service.regenerate_summary_block("file-1", "title", user_id="user-1")

    assert repo.acquire_statuses == []


@pytest.mark.asyncio
async def test_partial_regeneration_restores_previous_status_after_failure(monkeypatch):
    service, session, repo = _service(monkeypatch, acquired=True)
    monkeypatch.setattr(
        summary_runner,
        "regenerate_block",
        AsyncMock(side_effect=RuntimeError("LLM failed")),
    )

    with pytest.raises(RuntimeError, match="LLM failed"):
        await service.regenerate_summary_block("file-1", "title", user_id="user-1")

    assert session.rollbacks == 1
    assert repo.restored == [("file-1", FileStatus.COMPLETED)]
