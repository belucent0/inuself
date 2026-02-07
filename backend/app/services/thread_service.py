"""AI 채팅 스레드 서비스.

Redis를 사용하여 대화 스레드를 관리합니다.

V8.5: conversation_id → thread_id 용어 통일
- OpenAI Assistants, LangGraph 표준에 맞춤
- Langfuse에서는 session_id로 매핑
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from ..core.redis import get_redis_client

logger = logging.getLogger(__name__)

# Redis 키 프리픽스
THREAD_PREFIX = "ai:thread:"
THREAD_LIST_KEY = "ai:threads"
THREAD_TTL = 60 * 60 * 24 * 7  # 7일


class Message:
    """대화 메시지."""

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: float | None = None,
        metadata: dict | None = None,
    ):
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now().timestamp()
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """딕셔너리에서 생성."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
        )


class Thread:
    """대화 스레드."""

    def __init__(
        self,
        thread_id: str | None = None,
        title: str | None = None,
        messages: list[Message] | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        metadata: dict | None = None,
    ):
        self.thread_id = thread_id or str(uuid.uuid4())
        self.title = title or "새 대화"
        self.messages = messages or []
        self.created_at = created_at or datetime.now().timestamp()
        self.updated_at = updated_at or self.created_at
        self.metadata = metadata or {}

    def add_message(self, role: str, content: str, metadata: dict | None = None) -> Message:
        """메시지 추가."""
        message = Message(role=role, content=content, metadata=metadata)
        self.messages.append(message)
        self.updated_at = message.timestamp

        # 첫 사용자 메시지로 제목 자동 설정
        if self.title == "새 대화" and role == "user" and content:
            self.title = content[:50] + ("..." if len(content) > 50 else "")

        return message

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Thread":
        """딕셔너리에서 생성."""
        return cls(
            thread_id=data.get("thread_id"),
            title=data.get("title", "새 대화"),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            metadata=data.get("metadata", {}),
        )


class ThreadService:
    """AI 채팅 스레드 관리 서비스."""

    def __init__(self, redis_client: Any = None):
        """초기화.

        Args:
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._redis = redis_client

    @property
    def redis(self):
        """Redis 클라이언트 (지연 초기화)."""
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    async def create_thread(self, metadata: dict | None = None) -> Thread:
        """새 스레드 생성.

        Args:
            metadata: 추가 메타데이터

        Returns:
            생성된 스레드 객체
        """
        thread = Thread(metadata=metadata)

        # Redis에 저장
        key = f"{THREAD_PREFIX}{thread.thread_id}"
        await self.redis.set(key, json.dumps(thread.to_dict()), ex=THREAD_TTL)

        # 스레드 목록에 추가
        await self.redis.zadd(
            THREAD_LIST_KEY,
            {thread.thread_id: thread.created_at}
        )

        logger.info(f"[Thread] Created: {thread.thread_id}")
        return thread

    async def get_thread(self, thread_id: str) -> Thread | None:
        """스레드 조회.

        Args:
            thread_id: 스레드 ID

        Returns:
            스레드 객체 또는 None
        """
        key = f"{THREAD_PREFIX}{thread_id}"
        data = await self.redis.get(key)

        if not data:
            return None

        try:
            return Thread.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[Thread] Failed to parse: {thread_id}, error={e}")
            return None

    async def update_thread(self, thread: Thread) -> None:
        """스레드 업데이트.

        Args:
            thread: 업데이트할 스레드 객체
        """
        key = f"{THREAD_PREFIX}{thread.thread_id}"
        await self.redis.set(key, json.dumps(thread.to_dict()), ex=THREAD_TTL)

        # 스레드 목록 업데이트 (최근 사용순)
        await self.redis.zadd(
            THREAD_LIST_KEY,
            {thread.thread_id: thread.updated_at}
        )

        logger.debug(f"[Thread] Updated: {thread.thread_id}")

    async def delete_thread(self, thread_id: str) -> bool:
        """스레드 삭제.

        Args:
            thread_id: 스레드 ID

        Returns:
            삭제 성공 여부
        """
        key = f"{THREAD_PREFIX}{thread_id}"
        deleted = await self.redis.delete(key)

        # 스레드 목록에서 제거
        await self.redis.zrem(THREAD_LIST_KEY, thread_id)

        logger.info(f"[Thread] Deleted: {thread_id}")
        return deleted > 0

    async def list_threads(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """스레드 목록 조회.

        Args:
            limit: 조회 개수
            offset: 오프셋

        Returns:
            스레드 요약 목록
        """
        thread_ids = await self.redis.zrevrange(
            THREAD_LIST_KEY,
            offset,
            offset + limit - 1,
        )

        if not thread_ids:
            return []

        # 각 스레드의 요약 정보 조회
        results = []
        for tid in thread_ids:
            if isinstance(tid, bytes):
                tid = tid.decode()

            thread = await self.get_thread(tid)
            if thread:
                results.append({
                    "thread_id": thread.thread_id,
                    "title": thread.title,
                    "message_count": len(thread.messages),
                    "created_at": thread.created_at,
                    "updated_at": thread.updated_at,
                })

        return results

    async def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> Message:
        """스레드에 메시지 추가.

        Args:
            thread_id: 스레드 ID
            role: 메시지 역할 (user/assistant)
            content: 메시지 내용
            metadata: 추가 메타데이터

        Returns:
            추가된 메시지

        Raises:
            ValueError: 스레드를 찾을 수 없음
        """
        thread = await self.get_thread(thread_id)
        if not thread:
            raise ValueError(f"Thread not found: {thread_id}")

        message = thread.add_message(role, content, metadata)
        await self.update_thread(thread)

        return message

    async def remove_last_assistant_message(self, thread_id: str) -> str | None:
        """스레드에서 마지막 assistant 메시지 삭제 (재생성용).

        Args:
            thread_id: 스레드 ID

        Returns:
            삭제된 메시지에 대응하는 마지막 사용자 쿼리 (재생성에 사용)

        Raises:
            ValueError: 스레드를 찾을 수 없음
        """
        thread = await self.get_thread(thread_id)
        if not thread:
            raise ValueError(f"Thread not found: {thread_id}")

        # 마지막 메시지가 assistant인지 확인
        if not thread.messages or thread.messages[-1].role != "assistant":
            return None

        # 마지막 assistant 메시지 제거
        thread.messages.pop()

        # 마지막 user 메시지 내용 반환 (있으면)
        last_user_query = None
        for msg in reversed(thread.messages):
            if msg.role == "user":
                last_user_query = msg.content
                break

        await self.update_thread(thread)
        logger.info(f"[Thread] Removed last assistant message: {thread_id}")

        return last_user_query

    async def get_or_create_thread(
        self,
        thread_id: str | None,
        metadata: dict | None = None,
    ) -> Thread:
        """스레드 조회 또는 생성.

        Args:
            thread_id: 스레드 ID (None이면 새로 생성)
            metadata: 추가 메타데이터

        Returns:
            스레드 객체
        """
        if thread_id:
            thread = await self.get_thread(thread_id)
            if thread:
                return thread

        return await self.create_thread(metadata)


# 싱글톤 서비스 인스턴스
_thread_service: ThreadService | None = None


def get_thread_service() -> ThreadService:
    """스레드 서비스 싱글톤 인스턴스 반환."""
    global _thread_service
    if _thread_service is None:
        _thread_service = ThreadService()
    return _thread_service
