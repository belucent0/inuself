from typing import Sequence
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class ContentRepository:
    """콘텐츠 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_contents(self, limit: int = 20, offset: int = 0) -> Sequence[models.Content]:
        stmt = (
            select(models.Content)
            .order_by(models.Content.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_content(self, content_id: int) -> models.Content | None:
        stmt = (
            select(models.Content)
            .options(
                selectinload(models.Content.logs),
                selectinload(models.Content.llm_logs),
            )
            .where(models.Content.id == content_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_content(
        self,
        *,
        filename: str,
        object_key: str,
        speakers: list[str] | None = None,
        duration_seconds: float = 0.0,
        transcription: dict | None = None,
        status: models.ContentStatus | None = None,
    ) -> models.Content:
        content = models.Content(
            filename=filename,
            object_key=object_key,
            speakers=speakers or [],
            duration_seconds=duration_seconds,
            transcription=transcription or {},
            status=status or models.ContentStatus.QUEUED,
        )
        self.session.add(content)
        await self.session.flush()
        return content

    async def add_log(self, content_id: int, log: dict, message: str = "") -> models.SttLog:
        entry = models.SttLog(content_id=content_id, log=log, message=message)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def add_llm_log(self, content_id: int, log: dict, message: str = "") -> models.LlmLog:
        entry = models.LlmLog(content_id=content_id, log=log, message=message)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def update_content_status(
        self, content_id: int, status: models.ContentStatus
    ) -> None:
        """콘텐츠 상태 업데이트."""
        stmt = update(models.Content).where(models.Content.id == content_id).values(status=status)
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_content_result(
        self,
        content_id: int,
        *,
        speakers: list[str],
        duration_seconds: float,
        transcription: dict,
    ) -> models.Content:
        content = await self.session.get(models.Content, content_id)
        if not content:
            raise ValueError("Content not found")
        content.speakers = speakers
        content.duration_seconds = duration_seconds
        content.transcription = transcription
        await self.session.flush()
        return content

    async def update_summary_markdown(self, content_id: int, summary_md: str) -> None:
        stmt = (
            update(models.Content)
            .where(models.Content.id == content_id)
            .values(summary_md=summary_md)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_title(self, content_id: int, title: str) -> None:
        """콘텐츠 제목 업데이트."""
        stmt = (
            update(models.Content)
            .where(models.Content.id == content_id)
            .values(title=title)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def delete_queued_contents(self) -> tuple[int, list[int], list[str]]:
        """QUEUED 상태인 모든 콘텐츠 삭제. (삭제된 개수, content_id 리스트, object_key 리스트) 반환."""
        stmt = select(models.Content).where(models.Content.status == models.ContentStatus.QUEUED)
        result = await self.session.execute(stmt)
        contents = result.scalars().all()
        
        count = len(contents)
        content_ids = [content.id for content in contents]
        object_keys = [content.object_key for content in contents]
        
        for content in contents:
            await self.session.delete(content)
        
        await self.session.flush()
        return count, content_ids, object_keys

    async def delete_contents_by_ids(self, content_ids: list[int]) -> tuple[list[int], list[str]]:
        """지정된 콘텐츠를 상태와 무관하게 삭제하고 삭제된 ID, object_key를 반환."""
        if not content_ids:
            return [], []

        stmt = select(models.Content).where(models.Content.id.in_(content_ids))
        result = await self.session.execute(stmt)
        contents = result.scalars().all()

        deleted_ids = [content.id for content in contents]
        object_keys = [content.object_key for content in contents]

        for content in contents:
            await self.session.delete(content)

        await self.session.flush()
        return deleted_ids, object_keys

