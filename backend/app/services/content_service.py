from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.storage import delete_file, upload_fileobj
from ..db.models import ContentStatus
from ..repositories.content_repository import ContentRepository
from ..schemas.content import ContentDetail, ContentListItem, UploadResponse
from ..worker.queue import cancel_jobs_by_content_ids, enqueue_transcription_job

logger = logging.getLogger(__name__)


class ContentService:
    """콘텐츠 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ContentRepository(session)
        self.settings = get_settings()

    async def list_contents(self, limit: int = 20, offset: int = 0) -> Sequence[ContentListItem]:
        rows = await self.repo.list_contents(limit=limit, offset=offset)
        return [ContentListItem.model_validate(row) for row in rows]

    async def get_content(self, content_id: int) -> ContentDetail:
        content = await self.repo.get_content(content_id)
        if not content:
            raise ValueError("Content not found")
        # lazy load logs
        await self.session.refresh(content)
        return ContentDetail.model_validate(content)

    async def upload_and_enqueue(self, file: UploadFile) -> UploadResponse:
        object_key = self._build_object_key(file.filename)
        await self._upload_to_storage(file, object_key)
        content = await self.repo.create_content(
            filename=file.filename,
            object_key=object_key,
            speakers=[],
            transcription={},
            duration_seconds=0.0,
            status=ContentStatus.QUEUED,
        )
        await self.session.commit()

        enqueue_transcription_job(
            content_id=content.id,
            original_filename=file.filename,
            storage_key=object_key,
            model_size=self.settings.whisper_model_default,
            processing_mode="case4",
            num_asr_chunks=self.settings.max_workers,
        )

        return UploadResponse(content_id=content.id, queued=True)

    async def _upload_to_storage(self, file: UploadFile, object_key: str) -> None:
        # 파일 내용을 메모리에 읽어서 저장 (파일 포인터 문제 방지)
        file_content = await file.read()
        await file.close()
        
        # BytesIO로 변환하여 upload_fileobj에 전달
        from io import BytesIO
        file_obj = BytesIO(file_content)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: upload_fileobj(file_obj, key=object_key))

    async def delete_queued_contents(self) -> int:
        """QUEUED 상태인 모든 콘텐츠 삭제 (DB + 스토리지 + 큐)."""
        count, content_ids, object_keys = await self.repo.delete_queued_contents()
        await self._cleanup_queue_and_storage(content_ids, object_keys)
        await self.session.commit()
        return count

    async def delete_contents_by_ids(self, content_ids: list[int]) -> tuple[list[int], list[int]]:
        """
        주어진 ID의 콘텐츠를 상태와 무관하게 삭제하고,
        (deleted_ids, skipped_ids) 튜플을 반환한다.
        """
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return [], []

        deleted_ids, object_keys = await self.repo.delete_contents_by_ids(unique_ids)
        await self._cleanup_queue_and_storage(deleted_ids, object_keys)
        await self.session.commit()

        deleted_set = set(deleted_ids)
        skipped_ids = [content_id for content_id in unique_ids if content_id not in deleted_set]
        return deleted_ids, skipped_ids

    def _build_object_key(self, filename: str) -> str:
        """안전한 파일명으로 object_key 생성 (비ASCII 문자 제거)."""
        original_path = Path(filename)
        extension = original_path.suffix  # .mp4, .wav 등
        # UUID + 확장자로 안전한 파일명 생성 (비ASCII 문자 문제 해결)
        safe_filename = f"{uuid4().hex}{extension}"
        return f"{self.settings.s3_prefix}/{safe_filename}"

    async def _cleanup_queue_and_storage(self, content_ids: list[int], object_keys: list[str]) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if content_ids:
            cancelled_count = await loop.run_in_executor(None, cancel_jobs_by_content_ids, content_ids)
            if cancelled_count:
                logger.info("Cancelled %s jobs for deleted contents", cancelled_count)

        for object_key in object_keys:
            try:
                await loop.run_in_executor(None, delete_file, object_key)
            except Exception as exc:
                logger.warning("Failed to delete file from storage: %s, error: %s", object_key, exc)

