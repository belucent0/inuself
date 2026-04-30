from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from ..core.logging import logger
from sqlalchemy import select, update, func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..state_machines import InvalidTransitionError, TransitionContext, TransitionResult
from ..state_machines.machines import ContentStateMachine


class FileRepository:
    """파일 CRUD 쿼리 집합."""

    # 상태 머신 싱글톤
    _state_machine = ContentStateMachine()

    def __init__(self, session: AsyncSession):
        self.session = session

    @property
    def state_machine(self) -> ContentStateMachine:
        """상태 머신 인스턴스."""
        return self._state_machine

    async def list_files(
        self,
        user_id: UUID | None = None,
        limit: int = 20,
        offset: int = 0,
        content_type: models.ContentType | None = None,
        search: str | None = None,
    ) -> Sequence[models.File]:
        """파일 목록 조회 (관계 포함). user_id가 제공되면 해당 사용자의 파일만 조회."""
        stmt = (
            select(models.File)
            .join(models.Content, models.File.content)
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

        if user_id:
            stmt = stmt.where(models.Content.user_id == user_id)

        if content_type:
            stmt = stmt.where(models.File.content_type == content_type)

        if search:
            stmt = stmt.where(
                or_(
                    models.File.filename.ilike(f"%{search}%"),
                    models.Content.title.ilike(f"%{search}%"),
                )
            )

        stmt = stmt.limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_files(
        self,
        user_id: UUID | None = None,
        content_type: models.ContentType | None = None,
        search: str | None = None,
    ) -> int:
        """전체 파일 개수 조회. user_id가 제공되면 해당 사용자의 파일만 카운트."""
        stmt = select(func.count(models.File.id)).join(
            models.Content, models.File.content
        )

        if user_id:
            stmt = stmt.where(models.Content.user_id == user_id)

        if content_type:
            stmt = stmt.where(models.File.content_type == content_type)

        if search:
            stmt = stmt.where(
                or_(
                    models.File.filename.ilike(f"%{search}%"),
                    models.Content.title.ilike(f"%{search}%"),
                )
            )

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
                selectinload(models.File.content).selectinload(models.Content.logs),
                selectinload(models.File.content).selectinload(models.Content.llm_logs),
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
        user_id: UUID,
        status: models.FileStatus | None = None,
        size_bytes: int | None = None,
        mime_type: str | None = None,
        source_url: str | None = None,
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
            source_url=source_url,
            created_at=now,
        )
        self.session.add(file)
        await self.session.flush()

        # Content 생성 (user_id 포함)
        content = models.Content(
            file_id=file.id,
            user_id=user_id,
            status=effective_status,
            created_at=now,
            updated_at=now,
        )
        self.session.add(content)
        await self.session.flush()

        return file

    async def get_content_status(self, file_id: UUID) -> models.FileStatus | None:
        """파일의 현재 상태 조회."""
        stmt = select(models.Content.status).where(models.Content.file_id == file_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_file_status(
        self,
        file_id: UUID,
        status: models.FileStatus,
        *,
        triggered_by: str = "system",
        validate: bool = True,
        **metadata,
    ) -> TransitionResult:
        """파일 상태 업데이트 (StateMachine 검증 포함).

        Args:
            file_id: 파일 ID
            status: 목표 상태
            triggered_by: 전이를 트리거한 주체 (로깅용)
            validate: 상태 전이 검증 여부 (기본 True)
            **metadata: 추가 메타데이터 (로깅용)

        Returns:
            TransitionResult: 전이 결과

        Raises:
            InvalidTransitionError: 유효하지 않은 상태 전이 시 (validate=True일 때)
        """
        now = datetime.now(timezone.utc)

        # 현재 상태 조회
        current_status = await self.get_content_status(file_id)
        if current_status is None:
            return TransitionResult(
                success=False,
                old_state=None,
                new_state=None,
                reason=f"Content not found for file_id={file_id}",
            )

        # 같은 상태로의 전이는 no-op
        if current_status == status:
            return TransitionResult(
                success=True,
                old_state=current_status,
                new_state=status,
                reason="Already in target state",
            )

        # 상태 전이 검증
        if validate:
            can_transit, reason = self.state_machine.can_transition(
                current_status, status
            )
            if not can_transit:
                raise InvalidTransitionError(
                    current_state=current_status,
                    target_state=status,
                    reason=reason,
                    entity_id=file_id,
                )

        # DB 업데이트
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

        # 로깅
        logger.info(
            f"[StateMachine] Content({file_id}) "
            f"{current_status.value} → {status.value} "
            f"[triggered_by={triggered_by}]"
        )

        return TransitionResult(
            success=True,
            old_state=current_status,
            new_state=status,
        )

    async def update_object_key(self, file_id: UUID, object_key: str) -> None:
        """파일 object_key 업데이트."""
        stmt = (
            update(models.File)
            .where(models.File.id == file_id)
            .values(object_key=object_key)
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

    async def update_cover_image_key(self, file_id: UUID, cover_image_key: str) -> None:
        """커버 이미지 S3 key 업데이트 (Content만 업데이트)."""
        now = datetime.now(timezone.utc)

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(cover_image_key=cover_image_key, updated_at=now)
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

    async def delete_files_by_ids(
        self, file_ids: list[UUID], user_id: UUID | None = None
    ) -> tuple[list[UUID], list[str]]:
        """지정된 파일을 상태와 무관하게 삭제하고 삭제된 ID, object_key를 반환.
        user_id가 제공되면 해당 사용자의 파일만 삭제 가능."""
        if not file_ids:
            return [], []

        stmt = (
            select(models.File)
            .join(models.Content, models.File.content)
            .where(models.File.id.in_(file_ids))
        )

        if user_id:
            stmt = stmt.where(models.Content.user_id == user_id)

        result = await self.session.execute(stmt)
        files = result.scalars().all()

        deleted_ids = [file.id for file in files]
        object_keys = [file.object_key for file in files]

        for file in files:
            await self.session.delete(file)

        await self.session.flush()
        return deleted_ids, object_keys

    async def add_log(
        self, file_id: UUID, log: dict, message: str = ""
    ) -> models.SttLog | None:
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

    async def add_llm_log(
        self, file_id: UUID, log: dict, message: str = ""
    ) -> models.LlmLog | None:
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

    async def update_embedding(self, file_id: UUID, embedding: list[float]) -> None:
        """파일 임베딩 업데이트 (Content 테이블).

        Args:
            file_id: 파일 ID
            embedding: 임베딩 벡터 (768차원)
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        stmt = (
            update(models.Content)
            .where(models.Content.file_id == file_id)
            .values(embedding=embedding, updated_at=now)
        )
        await self.session.execute(stmt)
        await self.session.flush()
