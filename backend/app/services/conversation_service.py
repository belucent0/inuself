"""대화 히스토리 서비스.

Redis를 사용하여 대화 히스토리를 관리합니다.
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
CONVERSATION_PREFIX = "ai:conversation:"
CONVERSATION_LIST_KEY = "ai:conversations"
CONVERSATION_TTL = 60 * 60 * 24 * 7  # 7일


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


class Conversation:
    """대화 세션."""

    def __init__(
        self,
        conversation_id: str | None = None,
        title: str | None = None,
        messages: list[Message] | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        metadata: dict | None = None,
    ):
        self.conversation_id = conversation_id or str(uuid.uuid4())
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
            "conversation_id": self.conversation_id,
            "title": self.title,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        """딕셔너리에서 생성."""
        return cls(
            conversation_id=data["conversation_id"],
            title=data.get("title", "새 대화"),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            metadata=data.get("metadata", {}),
        )


class ConversationService:
    """대화 히스토리 관리 서비스."""

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

    async def create_conversation(self, metadata: dict | None = None) -> Conversation:
        """새 대화 생성.

        Args:
            metadata: 추가 메타데이터

        Returns:
            생성된 대화 객체
        """
        conversation = Conversation(metadata=metadata)

        # Redis에 저장
        key = f"{CONVERSATION_PREFIX}{conversation.conversation_id}"
        await self.redis.set(key, json.dumps(conversation.to_dict()), ex=CONVERSATION_TTL)

        # 대화 목록에 추가
        await self.redis.zadd(
            CONVERSATION_LIST_KEY,
            {conversation.conversation_id: conversation.created_at}
        )

        logger.info(f"[Conversation] Created: {conversation.conversation_id}")
        return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """대화 조회.

        Args:
            conversation_id: 대화 ID

        Returns:
            대화 객체 또는 None
        """
        key = f"{CONVERSATION_PREFIX}{conversation_id}"
        data = await self.redis.get(key)

        if not data:
            return None

        try:
            return Conversation.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[Conversation] Failed to parse: {conversation_id}, error={e}")
            return None

    async def update_conversation(self, conversation: Conversation) -> None:
        """대화 업데이트.

        Args:
            conversation: 업데이트할 대화 객체
        """
        key = f"{CONVERSATION_PREFIX}{conversation.conversation_id}"
        await self.redis.set(key, json.dumps(conversation.to_dict()), ex=CONVERSATION_TTL)

        # 대화 목록 업데이트 (최근 사용순)
        await self.redis.zadd(
            CONVERSATION_LIST_KEY,
            {conversation.conversation_id: conversation.updated_at}
        )

        logger.debug(f"[Conversation] Updated: {conversation.conversation_id}")

    async def delete_conversation(self, conversation_id: str) -> bool:
        """대화 삭제.

        Args:
            conversation_id: 대화 ID

        Returns:
            삭제 성공 여부
        """
        key = f"{CONVERSATION_PREFIX}{conversation_id}"
        deleted = await self.redis.delete(key)

        # 대화 목록에서 제거
        await self.redis.zrem(CONVERSATION_LIST_KEY, conversation_id)

        logger.info(f"[Conversation] Deleted: {conversation_id}")
        return deleted > 0

    async def list_conversations(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """대화 목록 조회.

        Args:
            limit: 조회 개수
            offset: 오프셋

        Returns:
            대화 요약 목록
        """
        # 최신순으로 대화 ID 조회
        conversation_ids = await self.redis.zrevrange(
            CONVERSATION_LIST_KEY,
            offset,
            offset + limit - 1,
        )

        if not conversation_ids:
            return []

        # 각 대화의 요약 정보 조회
        results = []
        for conv_id in conversation_ids:
            if isinstance(conv_id, bytes):
                conv_id = conv_id.decode()

            conversation = await self.get_conversation(conv_id)
            if conversation:
                results.append({
                    "conversation_id": conversation.conversation_id,
                    "title": conversation.title,
                    "message_count": len(conversation.messages),
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                })

        return results

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> Message:
        """대화에 메시지 추가.

        Args:
            conversation_id: 대화 ID
            role: 메시지 역할 (user/assistant)
            content: 메시지 내용
            metadata: 추가 메타데이터

        Returns:
            추가된 메시지

        Raises:
            ValueError: 대화를 찾을 수 없음
        """
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        message = conversation.add_message(role, content, metadata)
        await self.update_conversation(conversation)

        return message

    async def get_or_create_conversation(
        self,
        conversation_id: str | None,
        metadata: dict | None = None,
    ) -> Conversation:
        """대화 조회 또는 생성.

        Args:
            conversation_id: 대화 ID (None이면 새로 생성)
            metadata: 추가 메타데이터

        Returns:
            대화 객체
        """
        if conversation_id:
            conversation = await self.get_conversation(conversation_id)
            if conversation:
                return conversation

        return await self.create_conversation(metadata)


# 싱글톤 서비스 인스턴스
_conversation_service: ConversationService | None = None


def get_conversation_service() -> ConversationService:
    """대화 서비스 싱글톤 인스턴스 반환."""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service
