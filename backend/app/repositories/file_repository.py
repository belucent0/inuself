from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class FileRepository:
    """파일 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_files(
        self,
        limit: int = 20,
        offset: int = 0,
        content_type: models.ContentType | None = None,
    ) -> Sequence[models.File]:
        """파일 목록 조회 (관계 포함)."""
        stmt = (
            select(models.File)
            .options(
                selectinload(models.File.content).selectinload(
                    models.Content.transcription_result
                ),
                selectinload(models.File.content).selectinload(
                    models.Content.document_result
                ),
            )
            .order_by(models.File.created_at.desc())
        )

        if content_type:
            stmt = stmt.where(models.File.content_type == content_type)

        stmt = stmt.limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_files(self, content_type: models.ContentType | None = None) -> int:
        """전체 파일 개수 조회."""
        stmt = select(func.count(models.File.id))

        if content_type:
            stmt = stmt.where(models.File.content_type == content_type)

        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def get_file(self, file_id: UUID) -> models.File | None:
        """파일 조회 (관계 포함)."""
        stmt = (
            select(models.File)
            .options(
                selectinload(models.File.content).selectinload(
                    models.Content.transcription_result
                ),
                selectinload(models.File.content).selectinload(
                    models.Content.document_result
                ),
                selectinload(models.File.content).selectinload(
                    models.Content.logs
                ),
                selectinload(models.File.content).selectinload(
                    models.Content.llm_logs
                ),
            )
            .where(models.File.id == file_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_file(
        self,
        *,
        filename: str,
        object_key: str,
        content_type: models.ContentType,
        status: models.FileStatus | None = None,
        size_bytes: int | None = None,
        mime_type: str | None = None,
    ) -> models.File:
        """파일 생성 (Content도 함께 생성)."""
        effective_status = status or models.FileStatus.QUEUED
        now = datetime.now(timezone.utc)

        file = models.File(
            filename=filename,
            object_key=object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            mime_type=mime_type,
            created_at=now,
        )
        self.session.add(file)
        await self.session.flush()

        # Content 생성
        content = models.Content(
            file_id=file.id,
            status=effective_status,
            created_at=now,
            updated_at=now,
        )
        self.session.add(content)
        await self.session.flush()

        return file

    async def update_file_status(
        self, file_id: UUID, status: models.FileStatus
    ) -> None:
        """파일 상태 업데이트 (Content만 업데이트)."""
        now = datetime.now(timezone.utc)

        content_values = {"status": status, "updated_at": now}
        if status == models.FileStatus.COMPLETED:
            content_values["completed_at"] = now

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(**content_values)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_summary_markdown(self, file_id: UUID, summary_md: str) -> None:
        """요약 마크다운 업데이트 (Content만 업데이트)."""
        now = datetime.now(timezone.utc)

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(summary_md=summary_md, updated_at=now)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_title(self, file_id: UUID, title: str) -> None:
        """파일 제목 업데이트 (Content만 업데이트)."""
        now = datetime.now(timezone.utc)

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(title=title, updated_at=now)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def delete_queued_files(self) -> tuple[int, list[int], list[str]]:
        """QUEUED 상태인 모든 파일 삭제. (삭제된 개수, file_id 리스트, object_key 리스트) 반환."""
        # Content의 status를 기준으로 조회
        stmt = (
            select(models.File)
            .join(models.Content, models.Content.file_id == models.File.id)
            .where(models.Content.status == models.FileStatus.QUEUED)
        )
        result = await self.session.execute(stmt)
        files = result.scalars().all()

        count = len(files)
        file_ids = [file.id for file in files]
        object_keys = [file.object_key for file in files]

        for file in files:
            await self.session.delete(file)

        await self.session.flush()
        return count, file_ids, object_keys

    async def delete_files_by_ids(self, file_ids: list[UUID]) -> tuple[list[UUID], list[str]]:
        """지정된 파일을 상태와 무관하게 삭제하고 삭제된 ID, object_key를 반환."""
        if not file_ids:
            return [], []

        stmt = select(models.File).where(models.File.id.in_(file_ids))
        result = await self.session.execute(stmt)
        files = result.scalars().all()

        deleted_ids = [file.id for file in files]
        object_keys = [file.object_key for file in files]

        for file in files:
            await self.session.delete(file)

        await self.session.flush()
        return deleted_ids, object_keys

    async def add_log(self, file_id: UUID, log: dict, message: str = "") -> models.SttLog | None:
        """로그 추가. file이 존재하지 않으면 None 반환."""
        file = await self.get_file(file_id)
        if not file:
            return None

        # Content ID 조회
        content_id = file.content.id if file.content else None

        entry = models.SttLog(
            content_id=content_id,
            log=log,
            message=message,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def add_llm_log(self, file_id: UUID, log: dict, message: str = "") -> models.LlmLog | None:
        """LLM 로그 추가. file이 존재하지 않으면 None 반환."""
        file = await self.get_file(file_id)
        if not file:
            return None

        # Content ID 조회
        content_id = file.content.id if file.content else None

        entry = models.LlmLog(
            content_id=content_id,
            log=log,
            message=message,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry
