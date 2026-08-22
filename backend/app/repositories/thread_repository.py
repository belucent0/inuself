"""AI 대화 스레드 Repository.

Thread와 Message의 PostgreSQL CRUD 작업을 담당.
Redis 캐시는 ThreadService에서 처리.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import AiThread, AiMessage


class ThreadRepository:
    """AI 대화 스레드 데이터 접근 레이어."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ===== Thread CRUD =====

    async def create_thread(
        self,
        user_id: UUID,
        content_id: UUID | None = None,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> AiThread:
        """새 스레드 생성."""
        thread = AiThread(
            user_id=user_id,
            content_id=content_id,
            title=title or "새 대화",
            metadata_=metadata or {},
        )
        self.session.add(thread)
        await self.session.flush()
        return thread

    async def get_thread(
        self, thread_id: UUID, include_messages: bool = False
    ) -> AiThread | None:
        """스레드 조회."""
        stmt = select(AiThread).where(AiThread.id == thread_id)
        if include_messages:
            stmt = stmt.options(selectinload(AiThread.messages))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_thread_by_user(
        self, thread_id: UUID, user_id: UUID, include_messages: bool = False
    ) -> AiThread | None:
        """사용자 소유 스레드 조회 (권한 검증용)."""
        stmt = select(AiThread).where(
            AiThread.id == thread_id, AiThread.user_id == user_id
        )
        if include_messages:
            stmt = stmt.options(selectinload(AiThread.messages))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_content(
        self, user_id: UUID, content_id: UUID, limit: int = 10
    ) -> list[AiThread]:
        """특정 콘텐츠의 스레드 목록 (최근순, 메시지 포함)."""
        stmt = (
            select(AiThread)
            .where(
                AiThread.user_id == user_id,
                AiThread.content_id == content_id,
                AiThread.is_archived == False,
            )
            .options(selectinload(AiThread.messages))
            .order_by(desc(AiThread.updated_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_threads(
        self,
        user_id: UUID,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AiThread]:
        """사용자의 스레드 목록 조회 (최근 업데이트 순)."""
        stmt = select(AiThread).where(AiThread.user_id == user_id)

        if not include_archived:
            stmt = stmt.where(AiThread.is_archived == False)

        stmt = (
            stmt.order_by(desc(AiThread.updated_at))
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_threads(
        self, user_id: UUID, include_archived: bool = False
    ) -> int:
        """사용자의 스레드 개수."""
        stmt = select(func.count(AiThread.id)).where(AiThread.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(AiThread.is_archived == False)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def update_thread(
        self,
        thread: AiThread,
        title: str | None = None,
        metadata: dict | None = None,
        is_archived: bool | None = None,
    ) -> AiThread:
        """스레드 업데이트."""
        if title is not None:
            thread.title = title
        if metadata is not None:
            thread.metadata_ = metadata
        if is_archived is not None:
            thread.is_archived = is_archived
        thread.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return thread

    async def touch_thread(self, thread: AiThread) -> AiThread:
        """스레드 updated_at 갱신 (메시지 추가 시)."""
        thread.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return thread

    async def archive_thread(self, thread: AiThread) -> AiThread:
        """스레드 아카이브 (soft delete)."""
        thread.is_archived = True
        thread.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return thread

    async def delete_thread(self, thread: AiThread) -> None:
        """스레드 완전 삭제 (cascade로 메시지도 삭제)."""
        await self.session.delete(thread)
        await self.session.flush()

    # ===== Message CRUD =====

    async def add_message(
        self,
        thread_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
        status: str = "completed",
    ) -> AiMessage:
        """메시지 추가.

        Args:
            status: 메시지 상태 (pending, generating, completed, failed, cancelled)
                - user 메시지: 일반적으로 "completed"
                - assistant 메시지: 생성 시작 시 "generating", 완료 시 "completed"
        """
        message = AiMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            metadata_=metadata or {},
            status=status,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def get_message(self, message_id: UUID) -> AiMessage | None:
        """메시지 단일 조회."""
        stmt = select(AiMessage).where(AiMessage.id == message_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_message(self, message: AiMessage) -> None:
        await self.session.delete(message)
        await self.session.flush()

    async def update_message_status(
        self,
        message_id: UUID,
        status: str,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> AiMessage | None:
        """메시지 상태 업데이트.

        Args:
            message_id: 메시지 ID
            status: 새 상태 (generating, completed, failed, cancelled)
            content: 내용 업데이트 (선택, generating → completed 시 전체 내용)
            metadata: 메타데이터 업데이트 (선택, sources, thinking_steps 등)

        Returns:
            업데이트된 메시지 또는 None
        """
        message = await self.get_message(message_id)
        if not message:
            return None

        message.status = status
        if content is not None:
            message.content = content
        if metadata is not None:
            message.metadata_ = metadata

        await self.session.flush()
        return message

    async def update_message_metadata(
        self,
        message_id: UUID,
        **metadata_updates,
    ) -> AiMessage | None:
        """메시지 메타데이터 부분 업데이트 (병합).

        기존 메타데이터와 새 값을 병합합니다. 연결 끊김에도 메타데이터가
        보존되도록 이벤트 수신 즉시 호출합니다.

        Args:
            message_id: 메시지 ID
            **metadata_updates: 업데이트할 메타데이터 키-값 쌍
                (sources=..., thinking_steps=..., mode=..., etc.)

        Returns:
            업데이트된 메시지 또는 None
        """
        message = await self.get_message(message_id)
        if not message:
            return None

        # 기존 metadata와 병합 (새 값이 우선)
        current = message.metadata_ or {}
        current.update(metadata_updates)
        message.metadata_ = current

        await self.session.flush()
        return message

    async def update_message_partial_content(
        self,
        message_id: UUID,
        partial_content: str,
        status: str | None = None,
    ) -> AiMessage | None:
        """메시지 부분 응답 업데이트 (v1.0.0).

        스트리밍 중 2초마다 호출되어 부분 응답을 저장합니다.
        SSE 재연결 시 이 값부터 복구합니다.

        Args:
            message_id: 메시지 ID
            partial_content: 현재까지 생성된 부분 응답
            status: 현재 상태 (선택, queued|analyzing|searching|thinking|generating)

        Returns:
            업데이트된 메시지 또는 None
        """
        message = await self.get_message(message_id)
        if not message:
            return None

        message.partial_content = partial_content
        if status is not None:
            message.status = status

        await self.session.flush()
        return message

    async def get_generating_messages(self, thread_id: UUID) -> list[AiMessage]:
        """generating 상태의 메시지 조회.

        스트림 재연결 시 사용.
        """
        stmt = (
            select(AiMessage)
            .where(
                AiMessage.thread_id == thread_id,
                AiMessage.status == "generating",
            )
            .order_by(AiMessage.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_messages(
        self, thread_id: UUID, limit: int = 100, offset: int = 0
    ) -> list[AiMessage]:
        """스레드의 메시지 목록 조회 (시간순)."""
        stmt = (
            select(AiMessage)
            .where(AiMessage.thread_id == thread_id)
            .order_by(AiMessage.created_at)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_messages(
        self, thread_id: UUID, limit: int = 20
    ) -> list[AiMessage]:
        """스레드의 최근 메시지 (역순 조회 후 정렬)."""
        stmt = (
            select(AiMessage)
            .where(AiMessage.thread_id == thread_id)
            .order_by(desc(AiMessage.created_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        messages = list(result.scalars().all())
        return list(reversed(messages))  # 시간순으로 정렬

    async def count_messages(self, thread_id: UUID) -> int:
        """스레드의 메시지 개수."""
        stmt = select(func.count(AiMessage.id)).where(
            AiMessage.thread_id == thread_id
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_last_message(self, thread_id: UUID) -> AiMessage | None:
        """스레드의 마지막 메시지."""
        stmt = (
            select(AiMessage)
            .where(AiMessage.thread_id == thread_id)
            .order_by(desc(AiMessage.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_first_assistant_messages(self, thread_ids: list[UUID]) -> dict[UUID, str]:
        """각 스레드의 첫 AI 응답 메시지를 배치 조회."""
        if not thread_ids:
            return {}
        subq = (
            select(
                AiMessage.thread_id,
                AiMessage.content,
                func.row_number().over(
                    partition_by=AiMessage.thread_id,
                    order_by=AiMessage.created_at
                ).label('rn')
            )
            .where(AiMessage.thread_id.in_(thread_ids), AiMessage.role == 'assistant')
            .subquery()
        )
        stmt = select(subq.c.thread_id, subq.c.content).where(subq.c.rn == 1)
        result = await self.session.execute(stmt)
        return {row.thread_id: row.content[:120] for row in result}

    async def delete_last_assistant_message(
        self, thread_id: UUID
    ) -> str | None:
        """마지막 assistant 메시지 삭제 (재생성용).

        Returns:
            삭제 성공 시 마지막 user 메시지 내용, 실패 시 None
        """
        last_message = await self.get_last_message(thread_id)
        if not last_message or last_message.role != "assistant":
            return None

        await self.session.delete(last_message)
        await self.session.flush()

        # 마지막 user 메시지 찾기
        stmt = (
            select(AiMessage)
            .where(
                AiMessage.thread_id == thread_id,
                AiMessage.role == "user",
            )
            .order_by(desc(AiMessage.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        user_message = result.scalar_one_or_none()
        return user_message.content if user_message else None
