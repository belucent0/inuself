"""AI 채팅 스레드 서비스.

PostgreSQL 영속 저장 + Redis 캐시 레이어 구조.

V9.0 (Phase 1): Redis-only → PostgreSQL + Redis 캐시
- Write Path: DB 저장 → Redis 캐시 갱신
- Read Path: Redis 캐시 히트 → 없으면 DB 조회 → Redis 캐시 설정
- user_id 기반 사용자별 스레드 관리

V8.5: conversation_id → thread_id 용어 통일
- OpenAI Assistants, LangGraph 표준에 맞춤
- Langfuse에서는 session_id로 매핑
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import get_redis_client
from ..db.models import AiThread, AiMessage
from ..repositories.thread_repository import ThreadRepository

logger = logging.getLogger(__name__)

# Redis 캐시 설정
THREAD_CACHE_PREFIX = "ai:thread:"
THREAD_LIST_PREFIX = "ai:threads:"  # ai:threads:{user_id}
CACHE_TTL = 60 * 60 * 24  # 24시간 (캐시 TTL, DB에는 영구 저장)


class Message:
    """대화 메시지 (API 응답용 DTO).

    v1.0.0: partial_content 추가 (SSE 재연결 시 복구용)
    """

    def __init__(
        self,
        message_id: str | None = None,
        thread_id: str | None = None,
        role: str = "",
        content: str = "",
        timestamp: float | None = None,
        metadata: dict | None = None,
        status: str = "completed",
        partial_content: str | None = None,
    ):
        self.message_id = message_id
        self.thread_id = thread_id
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now(timezone.utc).timestamp()
        self.metadata = metadata or {}
        self.status = status
        self.partial_content = partial_content

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return {
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "status": self.status,
            "partial_content": self.partial_content,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """딕셔너리에서 생성."""
        return cls(
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id"),
            role=data["role"],
            content=data["content"],
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
            status=data.get("status", "completed"),
            partial_content=data.get("partial_content"),
        )

    @classmethod
    def from_db_model(cls, model: AiMessage) -> "Message":
        """DB 모델에서 생성."""
        return cls(
            message_id=str(model.id),
            thread_id=str(model.thread_id),
            role=model.role,
            content=model.content,
            timestamp=model.created_at.timestamp(),
            metadata=model.metadata_,
            status=model.status,
            partial_content=model.partial_content,
        )


class Thread:
    """대화 스레드 (API 응답용 DTO)."""

    def __init__(
        self,
        thread_id: str | None = None,
        user_id: str | None = None,
        content_id: str | None = None,
        title: str | None = None,
        messages: list[Message] | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        metadata: dict | None = None,
        is_archived: bool = False,
    ):
        self.thread_id = thread_id
        self.user_id = user_id
        self.content_id = content_id
        self.title = title or "새 대화"
        self.messages = messages or []
        self.created_at = created_at or datetime.now(timezone.utc).timestamp()
        self.updated_at = updated_at or self.created_at
        self.metadata = metadata or {}
        self.is_archived = is_archived

    def add_message(
        self, role: str, content: str, metadata: dict | None = None
    ) -> Message:
        """메시지 추가 (메모리에만, DB 저장은 서비스에서)."""
        message = Message(role=role, content=content, metadata=metadata)
        self.messages.append(message)
        self.updated_at = message.timestamp

        # 첫 사용자 메시지로 제목 자동 설정
        if self.title == "새 대화" and role == "user" and content:
            self.title = content[:50] + ("..." if len(content) > 50 else "")

        return message

    def to_dict(self) -> dict:
        """딕셔너리로 변환 (캐시용)."""
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "content_id": self.content_id,
            "title": self.title,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "is_archived": self.is_archived,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Thread":
        """딕셔너리에서 생성 (캐시에서)."""
        return cls(
            thread_id=data.get("thread_id"),
            user_id=data.get("user_id"),
            content_id=data.get("content_id"),
            title=data.get("title", "새 대화"),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            metadata=data.get("metadata", {}),
            is_archived=data.get("is_archived", False),
        )

    @classmethod
    def from_db_model(cls, model: AiThread, include_messages: bool = True) -> "Thread":
        """DB 모델에서 생성."""
        messages = []
        if include_messages and model.messages:
            messages = [Message.from_db_model(m) for m in model.messages]

        return cls(
            thread_id=str(model.id),
            user_id=str(model.user_id),
            content_id=str(model.content_id) if model.content_id else None,
            title=model.title,
            messages=messages,
            created_at=model.created_at.timestamp(),
            updated_at=model.updated_at.timestamp() if model.updated_at else model.created_at.timestamp(),
            metadata=model.metadata_,
            is_archived=model.is_archived,
        )


class ThreadService:
    """AI 채팅 스레드 관리 서비스.

    PostgreSQL 영속 저장 + Redis 캐시.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        redis_client: Any = None,
    ):
        """초기화.

        Args:
            session: SQLAlchemy 비동기 세션 (없으면 레거시 모드)
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._session = session
        self._redis = redis_client
        self._repo: ThreadRepository | None = None

    @property
    def redis(self):
        """Redis 클라이언트 (지연 초기화)."""
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    @property
    def repo(self) -> ThreadRepository | None:
        """ThreadRepository (세션이 있을 때만)."""
        if self._repo is None and self._session is not None:
            self._repo = ThreadRepository(self._session)
        return self._repo

    def _cache_key(self, thread_id: str) -> str:
        """스레드 캐시 키."""
        return f"{THREAD_CACHE_PREFIX}{thread_id}"

    def _list_cache_key(self, user_id: str) -> str:
        """사용자 스레드 목록 캐시 키."""
        return f"{THREAD_LIST_PREFIX}{user_id}"

    async def _set_cache(self, thread: Thread) -> None:
        """스레드를 캐시에 저장."""
        try:
            key = self._cache_key(thread.thread_id)
            await self.redis.set(key, json.dumps(thread.to_dict()), ex=CACHE_TTL)
        except Exception as e:
            logger.warning(f"[Thread] Cache set failed: {e}")

    async def _get_cache(self, thread_id: str) -> Thread | None:
        """캐시에서 스레드 조회."""
        try:
            key = self._cache_key(thread_id)
            data = await self.redis.get(key)
            if data:
                return Thread.from_dict(json.loads(data))
        except Exception as e:
            logger.warning(f"[Thread] Cache get failed: {e}")
        return None

    async def _invalidate_cache(self, thread_id: str, user_id: str | None = None) -> None:
        """캐시 무효화."""
        try:
            await self.redis.delete(self._cache_key(thread_id))
            if user_id:
                await self.redis.delete(self._list_cache_key(user_id))
        except Exception as e:
            logger.warning(f"[Thread] Cache invalidate failed: {e}")

    async def create_thread(
        self,
        user_id: str | UUID,
        content_id: str | UUID | None = None,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> Thread:
        """새 스레드 생성.

        Args:
            user_id: 사용자 ID
            content_id: 콘텐츠 ID (선택)
            title: 대화 제목 (선택)
            metadata: 추가 메타데이터

        Returns:
            생성된 스레드 객체
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        user_uuid = UUID(str(user_id))
        content_uuid = UUID(str(content_id)) if content_id else None

        db_thread = await self.repo.create_thread(
            user_id=user_uuid,
            content_id=content_uuid,
            title=title,
            metadata=metadata,
        )

        thread = Thread.from_db_model(db_thread, include_messages=False)

        # 캐시 설정
        await self._set_cache(thread)
        await self._invalidate_cache(thread.thread_id, str(user_id))

        logger.info(f"[Thread] Created: {thread.thread_id} for user {user_id}")
        return thread

    async def get_thread(
        self,
        thread_id: str | UUID,
        user_id: str | UUID | None = None,
        include_messages: bool = True,
    ) -> Thread | None:
        """스레드 조회.

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증용, 선택)
            include_messages: 메시지 포함 여부

        Returns:
            스레드 객체 또는 None
        """
        thread_id_str = str(thread_id)

        # 1. 캐시 확인 (메시지 포함된 경우)
        if include_messages:
            cached = await self._get_cache(thread_id_str)
            if cached:
                # 사용자 권한 검증
                if user_id and cached.user_id != str(user_id):
                    return None
                return cached

        # 2. DB 조회
        if not self.repo:
            raise RuntimeError("Database session not available")

        thread_uuid = UUID(thread_id_str)

        if user_id:
            db_thread = await self.repo.get_thread_by_user(
                thread_uuid, UUID(str(user_id)), include_messages=include_messages
            )
        else:
            db_thread = await self.repo.get_thread(
                thread_uuid, include_messages=include_messages
            )

        if not db_thread:
            return None

        thread = Thread.from_db_model(db_thread, include_messages=include_messages)

        # 3. 캐시 설정
        if include_messages:
            await self._set_cache(thread)

        return thread

    async def list_threads(
        self,
        user_id: str | UUID,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """스레드 목록 조회.

        Args:
            user_id: 사용자 ID
            include_archived: 아카이브 포함 여부
            limit: 조회 개수
            offset: 오프셋

        Returns:
            스레드 요약 목록
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        user_uuid = UUID(str(user_id))
        db_threads = await self.repo.list_threads(
            user_uuid,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )

        # 배치로 첫 AI 응답 메시지 조회
        thread_ids = [t.id for t in db_threads]
        first_messages = await self.repo.get_first_assistant_messages(thread_ids)

        results = []
        for t in db_threads:
            message_count = await self.repo.count_messages(t.id)
            results.append({
                "thread_id": str(t.id),
                "title": t.title,
                "message_count": message_count,
                "created_at": t.created_at.timestamp(),
                "updated_at": t.updated_at.timestamp() if t.updated_at else t.created_at.timestamp(),
                "is_archived": t.is_archived,
                "metadata": t.metadata_ or {},
                "first_message_preview": first_messages.get(t.id, ""),
            })

        return results

    async def count_threads(
        self,
        user_id: str | UUID,
        include_archived: bool = False,
    ) -> int:
        """사용자의 스레드 전체 개수 조회."""
        if not self.repo:
            raise RuntimeError("Database session not available")
        user_uuid = UUID(str(user_id))
        return await self.repo.count_threads(user_uuid, include_archived=include_archived)

    async def get_threads_by_content(
        self,
        user_id: str | UUID,
        content_id: str | UUID,
        limit: int = 10,
    ) -> list[dict]:
        """특정 콘텐츠의 스레드 목록 (최근순, 메시지 포함).

        Args:
            user_id: 사용자 ID
            content_id: 콘텐츠 ID (File.id)
            limit: 최대 조회 개수

        Returns:
            스레드 목록 (messages, metadata 포함)
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        user_uuid = UUID(str(user_id))
        content_uuid = UUID(str(content_id))

        db_threads = await self.repo.list_by_content(user_uuid, content_uuid, limit=limit)

        results = []
        for t in db_threads:
            thread = Thread.from_db_model(t, include_messages=True)
            result = thread.to_dict()
            result["message_count"] = len(thread.messages)
            results.append(result)

        return results

    async def update_thread_metadata(
        self,
        thread_id: str | UUID,
        user_id: str | UUID,
        metadata_patch: dict,
    ) -> Thread | None:
        """스레드 metadata 부분 업데이트 (기존 값과 병합).

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)
            metadata_patch: 병합할 메타데이터 키-값 쌍

        Returns:
            업데이트된 스레드 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        db_thread = await self.repo.get_thread_by_user(
            UUID(str(thread_id)), UUID(str(user_id))
        )
        if not db_thread:
            return None

        # 기존 metadata와 병합 (새 값이 우선)
        current = dict(db_thread.metadata_ or {})
        current.update(metadata_patch)
        db_thread = await self.repo.update_thread(db_thread, metadata=current)

        thread = Thread.from_db_model(db_thread, include_messages=False)

        # 캐시 무효화
        await self._invalidate_cache(str(thread_id), str(user_id))

        logger.debug(f"[Thread] Metadata updated: {thread_id}")
        return thread

    async def update_thread(
        self,
        thread_id: str | UUID,
        user_id: str | UUID,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> Thread | None:
        """스레드 업데이트.

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)
            title: 새 제목
            metadata: 새 메타데이터

        Returns:
            업데이트된 스레드 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        db_thread = await self.repo.get_thread_by_user(
            UUID(str(thread_id)), UUID(str(user_id))
        )
        if not db_thread:
            return None

        db_thread = await self.repo.update_thread(
            db_thread, title=title, metadata=metadata
        )

        thread = Thread.from_db_model(db_thread, include_messages=False)

        # 캐시 무효화
        await self._invalidate_cache(str(thread_id), str(user_id))

        logger.debug(f"[Thread] Updated: {thread_id}")
        return thread

    async def archive_thread(
        self, thread_id: str | UUID, user_id: str | UUID
    ) -> bool:
        """스레드 아카이브 (soft delete).

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)

        Returns:
            성공 여부
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        db_thread = await self.repo.get_thread_by_user(
            UUID(str(thread_id)), UUID(str(user_id))
        )
        if not db_thread:
            return False

        await self.repo.archive_thread(db_thread)
        await self._invalidate_cache(str(thread_id), str(user_id))

        logger.info(f"[Thread] Archived: {thread_id}")
        return True

    async def delete_thread(
        self, thread_id: str | UUID, user_id: str | UUID
    ) -> bool:
        """스레드 완전 삭제.

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)

        Returns:
            삭제 성공 여부
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        db_thread = await self.repo.get_thread_by_user(
            UUID(str(thread_id)), UUID(str(user_id))
        )
        if not db_thread:
            return False

        await self.repo.delete_thread(db_thread)
        await self._invalidate_cache(str(thread_id), str(user_id))

        logger.info(f"[Thread] Deleted: {thread_id}")
        return True

    async def add_message(
        self,
        thread_id: str | UUID,
        user_id: str | UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
        status: str = "completed",
    ) -> Message:
        """스레드에 메시지 추가.

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)
            role: 메시지 역할 (user/assistant)
            content: 메시지 내용
            metadata: 추가 메타데이터
            status: 메시지 상태 (pending, generating, completed, failed, cancelled)
                - user 메시지: 일반적으로 "completed"
                - assistant 메시지: 생성 시작 시 "generating", 완료 시 "completed"

        Returns:
            추가된 메시지

        Raises:
            ValueError: 스레드를 찾을 수 없음
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        thread_uuid = UUID(str(thread_id))
        user_uuid = UUID(str(user_id))

        # 권한 검증
        db_thread = await self.repo.get_thread_by_user(thread_uuid, user_uuid)
        if not db_thread:
            raise ValueError(f"Thread not found: {thread_id}")

        # 메시지 추가
        db_message = await self.repo.add_message(
            thread_uuid, role, content, metadata, status=status
        )

        # 스레드 updated_at 갱신 + 제목 자동 설정
        if db_thread.title == "새 대화" and role == "user" and content:
            new_title = content[:50] + ("..." if len(content) > 50 else "")
            await self.repo.update_thread(db_thread, title=new_title)
        else:
            await self.repo.touch_thread(db_thread)

        # 캐시 무효화
        await self._invalidate_cache(str(thread_id), str(user_id))

        return Message.from_db_model(db_message)

    async def update_message_status(
        self,
        message_id: str | UUID,
        status: str,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> Message | None:
        """메시지 상태 업데이트.

        Args:
            message_id: 메시지 ID
            status: 새 상태 (generating, completed, failed, cancelled)
            content: 내용 업데이트 (선택, generating → completed 시 전체 내용)
            metadata: 메타데이터 업데이트 (선택, sources, thinking_steps 등)

        Returns:
            업데이트된 메시지 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        message_uuid = UUID(str(message_id))
        db_message = await self.repo.update_message_status(
            message_uuid, status, content, metadata
        )

        if not db_message:
            return None

        # 캐시 무효화 (스레드 캐시 갱신)
        await self._invalidate_cache(str(db_message.thread_id))

        return Message.from_db_model(db_message)

    async def update_message_metadata(
        self,
        message_id: str | UUID,
        **metadata_updates,
    ) -> Message | None:
        """메시지 메타데이터 부분 업데이트 (병합).

        스트리밍 중 sources, thinking_steps 등을 이벤트 수신 즉시 저장합니다.
        클라이언트 연결이 끊겨도 메타데이터가 보존됩니다.

        Args:
            message_id: 메시지 ID
            **metadata_updates: 업데이트할 메타데이터 키-값 쌍
                (sources=..., thinking_steps=..., mode=..., etc.)

        Returns:
            업데이트된 메시지 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        message_uuid = UUID(str(message_id))
        db_message = await self.repo.update_message_metadata(
            message_uuid, **metadata_updates
        )

        if not db_message:
            return None

        # 캐시 무효화 (스레드 캐시 갱신)
        await self._invalidate_cache(str(db_message.thread_id))

        return Message.from_db_model(db_message)

    async def update_message_partial_content(
        self,
        message_id: str | UUID,
        partial_content: str,
        status: str | None = None,
    ) -> Message | None:
        """메시지 부분 응답 업데이트 (v1.0.0).

        스트리밍 중 2초마다 호출되어 부분 응답을 저장합니다.
        SSE 재연결 시 이 값부터 복구합니다.

        Args:
            message_id: 메시지 ID
            partial_content: 현재까지 생성된 부분 응답
            status: 현재 상태 (선택)

        Returns:
            업데이트된 메시지 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        message_uuid = UUID(str(message_id))
        db_message = await self.repo.update_message_partial_content(
            message_uuid, partial_content, status
        )

        if not db_message:
            return None

        # 캐시 무효화 (스레드 캐시 갱신)
        await self._invalidate_cache(str(db_message.thread_id))

        return Message.from_db_model(db_message)

    async def get_message(
        self,
        message_id: str | UUID,
    ) -> Message | None:
        """메시지 단일 조회 (v1.0.0).

        Args:
            message_id: 메시지 ID

        Returns:
            메시지 또는 None
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        message_uuid = UUID(str(message_id))
        db_message = await self.repo.get_message(message_uuid)

        if not db_message:
            return None

        return Message.from_db_model(db_message)

    async def get_last_message(
        self, thread_id: str | UUID, role: str | None = None
    ) -> Message | None:
        if not self.repo:
            raise RuntimeError("Database session not available")
        db_message = await self.repo.get_last_message(UUID(str(thread_id)), role=role)
        return Message.from_db_model(db_message) if db_message else None

    async def get_generating_messages(
        self, thread_id: str | UUID
    ) -> list[Message]:
        """generating 상태의 메시지 조회.

        스트림 재연결 시 사용. 해당 스레드에서 생성 중인 메시지가 있는지 확인.

        Args:
            thread_id: 스레드 ID

        Returns:
            generating 상태의 메시지 목록
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        thread_uuid = UUID(str(thread_id))
        db_messages = await self.repo.get_generating_messages(thread_uuid)
        return [Message.from_db_model(m) for m in db_messages]

    async def remove_last_assistant_message(
        self, thread_id: str | UUID, user_id: str | UUID
    ) -> str | None:
        """스레드에서 마지막 assistant 메시지 삭제 (재생성용).

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)

        Returns:
            삭제된 메시지에 대응하는 마지막 사용자 쿼리 (재생성에 사용)
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        thread_uuid = UUID(str(thread_id))
        user_uuid = UUID(str(user_id))

        # 권한 검증
        db_thread = await self.repo.get_thread_by_user(thread_uuid, user_uuid)
        if not db_thread:
            return None

        last_user_query = await self.repo.delete_last_assistant_message(thread_uuid)

        if last_user_query:
            await self._invalidate_cache(str(thread_id), str(user_id))
            logger.info(f"[Thread] Removed last assistant message: {thread_id}")

        return last_user_query

    async def get_or_create_thread(
        self,
        user_id: str | UUID,
        thread_id: str | UUID | None = None,
        content_id: str | UUID | None = None,
        metadata: dict | None = None,
    ) -> Thread:
        """스레드 조회 또는 생성.

        Args:
            user_id: 사용자 ID
            thread_id: 스레드 ID (None이면 새로 생성)
            content_id: 콘텐츠 ID (새로 생성 시)
            metadata: 추가 메타데이터

        Returns:
            스레드 객체
        """
        if thread_id:
            thread = await self.get_thread(thread_id, user_id)
            if thread:
                return thread

        return await self.create_thread(user_id, content_id, metadata=metadata)

    async def get_messages(
        self,
        thread_id: str | UUID,
        user_id: str | UUID,
        limit: int = 100,
    ) -> list[Message]:
        """스레드의 메시지 목록 조회.

        Args:
            thread_id: 스레드 ID
            user_id: 사용자 ID (권한 검증)
            limit: 최대 조회 개수

        Returns:
            메시지 목록
        """
        if not self.repo:
            raise RuntimeError("Database session not available")

        thread_uuid = UUID(str(thread_id))
        user_uuid = UUID(str(user_id))

        # 권한 검증
        db_thread = await self.repo.get_thread_by_user(thread_uuid, user_uuid)
        if not db_thread:
            return []

        db_messages = await self.repo.get_messages(thread_uuid, limit=limit)
        return [Message.from_db_model(m) for m in db_messages]


# 싱글톤 서비스는 제거 (세션 의존성 때문에 의미 없음)
# 대신 의존성 주입 사용: get_thread_service(session) 패턴


def get_thread_service(session: AsyncSession) -> ThreadService:
    """세션 기반 ThreadService 생성."""
    return ThreadService(session=session)
