from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID
from sqlalchemy import select, update, text, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class ContentRepository:
    """콘텐츠 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_contents(self, limit: int = 20, offset: int = 0) -> Sequence[models.Content]:
        """콘텐츠 목록 조회."""
        stmt = (
            select(models.Content)
            .options(
                selectinload(models.Content.file),
                selectinload(models.Content.transcription_result),
                selectinload(models.Content.document_result),
            )
            .order_by(models.Content.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_contents(self) -> int:
        """전체 콘텐츠 개수 조회."""
        stmt = select(func.count(models.Content.id))
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def get_content(self, content_id: UUID) -> models.Content | None:
        """Content ID (UUID)로 조회."""
        stmt = (
            select(models.Content)
            .options(
                selectinload(models.Content.file),
                selectinload(models.Content.transcription_result),
                selectinload(models.Content.document_result),
                selectinload(models.Content.logs),
                selectinload(models.Content.llm_logs),
            )
            .where(models.Content.id == content_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_file_id(self, file_id: UUID) -> models.Content | None:
        """File ID (UUID)로 Content 조회."""
        stmt = (
            select(models.Content)
            .options(
                selectinload(models.Content.file),
                selectinload(models.Content.transcription_result),
                selectinload(models.Content.document_result),
                selectinload(models.Content.logs),
                selectinload(models.Content.llm_logs),
            )
            .where(models.Content.file_id == file_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_content(
        self,
        *,
        file_id: UUID,
        status: models.ContentStatus | None = None,
    ) -> models.Content:
        """콘텐츠 생성 (File 기반).

        Args:
            file_id: 연결할 File의 ID (UUID, 필수)
            status: 초기 상태 (기본값: QUEUED)
        """
        now = datetime.now(timezone.utc)
        content = models.Content(
            file_id=file_id,
            status=status or models.ContentStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self.session.add(content)
        await self.session.flush()
        return content

    async def add_log(self, content_id: UUID, log: dict, message: str = "") -> models.SttLog | None:
        """로그 추가. content가 존재하지 않으면 None 반환."""
        content = await self.get_content(content_id)
        if not content:
            return None

        entry = models.SttLog(
            content_id=content_id,
            log=log,
            message=message,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def add_llm_log(self, content_id: UUID, log: dict, message: str = "") -> models.LlmLog | None:
        """LLM 로그 추가. content가 존재하지 않으면 None 반환."""
        content = await self.get_content(content_id)
        if not content:
            return None

        entry = models.LlmLog(
            content_id=content_id,
            log=log,
            message=message,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def update_content_status(
        self, content_id: UUID, status: models.ContentStatus
    ) -> None:
        """콘텐츠 상태 업데이트."""
        now = datetime.now(timezone.utc)
        values = {"status": status, "updated_at": now}
        if status == models.ContentStatus.COMPLETED:
            values["completed_at"] = now

        stmt = (
            update(models.Content)
            .where(models.Content.id == content_id)
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_status_by_file_id(
        self, file_id: UUID, status: models.ContentStatus
    ) -> None:
        """File ID (UUID)로 상태 업데이트."""
        now = datetime.now(timezone.utc)
        values = {"status": status, "updated_at": now}
        if status == models.ContentStatus.COMPLETED:
            values["completed_at"] = now

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_summary_markdown(self, content_id: UUID, summary_md: str) -> None:
        """요약 마크다운 업데이트."""
        stmt = (
            update(models.Content)
            .where(models.Content.id == content_id)
            .values(summary_md=summary_md, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_summary_by_file_id(self, file_id: UUID, summary_md: str) -> None:
        """File ID (UUID)로 요약 마크다운 업데이트."""
        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(summary_md=summary_md, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_title(self, content_id: UUID, title: str) -> None:
        """콘텐츠 제목 업데이트."""
        stmt = (
            update(models.Content)
            .where(models.Content.id == content_id)
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_title_by_file_id(self, file_id: UUID, title: str) -> None:
        """File ID (UUID)로 제목 업데이트."""
        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def delete_queued_contents(self) -> tuple[int, list[UUID], list[str]]:
        """QUEUED 상태인 모든 콘텐츠 삭제. (삭제된 개수, content_id 리스트, object_key 리스트) 반환."""
        stmt = (
            select(models.Content)
            .options(selectinload(models.Content.file))
            .where(models.Content.status == models.ContentStatus.QUEUED)
        )
        result = await self.session.execute(stmt)
        contents = result.scalars().all()

        count = len(contents)
        content_ids = [content.id for content in contents]
        # File에서 object_key 조회
        object_keys = [content.file.object_key for content in contents if content.file]

        for content in contents:
            await self.session.delete(content)

        await self.session.flush()
        return count, content_ids, object_keys

    async def delete_contents_by_ids(self, content_ids: list[UUID]) -> tuple[list[UUID], list[str]]:
        """지정된 콘텐츠를 상태와 무관하게 삭제하고 삭제된 ID, object_key를 반환."""
        if not content_ids:
            return [], []

        stmt = (
            select(models.Content)
            .options(selectinload(models.Content.file))
            .where(models.Content.id.in_(content_ids))
        )
        result = await self.session.execute(stmt)
        contents = result.scalars().all()

        deleted_ids = [content.id for content in contents]
        # File에서 object_key 조회
        object_keys = [content.file.object_key for content in contents if content.file]

        for content in contents:
            await self.session.delete(content)

        await self.session.flush()
        return deleted_ids, object_keys
