"""
Redis 대화 로더

Redis에서 대화 데이터를 로드하는 구현체
"""

from typing import List, Any
import redis
import json

from ..core.interfaces import IConversationLoader, ConversationData


class RedisConversationLoader(IConversationLoader):
    """
    Redis에서 대화 로드

    Redis 키 구조:
    - (legacy) ai:conversation:{conversation_id}: 대화 데이터 (JSON)
    - (legacy) ai:conversations: 대화 ID 목록 (Sorted Set, score=timestamp)
    - (current cache) ai:thread:{thread_id}: 스레드 데이터 (JSON)
    """

    LEGACY_CONVERSATION_PREFIX = "ai:conversation:"
    LEGACY_CONVERSATION_INDEX = "ai:conversations"
    THREAD_CACHE_PREFIX = "ai:thread:"

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        """
        Args:
            host: Redis 호스트
            port: Redis 포트
            db: Redis DB 번호
        """
        self.redis_client = redis.Redis(
            host=host, port=port, db=db, decode_responses=True
        )

    def load_conversation(self, conversation_id: str) -> ConversationData:
        """
        단일 대화 로드

        Args:
            conversation_id: 대화 ID

        Returns:
            ConversationData 객체

        Raises:
            ValueError: 대화를 찾을 수 없는 경우
        """
        key = f"{self.LEGACY_CONVERSATION_PREFIX}{conversation_id}"
        data = self.redis_client.get(key)

        if data:
            try:
                conv_dict = json.loads(data)
                return ConversationData(**conv_dict)
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(
                    f"Invalid legacy conversation data for {conversation_id}: {str(e)}"
                )

        # 현재 구조: ai:thread:{thread_id} 캐시 포맷 지원
        thread_conv = self._load_thread_from_cache(conversation_id)
        if thread_conv is not None:
            return thread_conv

        raise ValueError(f"Conversation/Thread {conversation_id} not found in Redis")

    def load_conversations(self, conversation_ids: List[str]) -> List[ConversationData]:
        """
        여러 대화 로드

        실패한 대화는 건너뛰고 경고만 출력

        Args:
            conversation_ids: 대화 ID 목록

        Returns:
            ConversationData 객체 리스트
        """
        conversations = []
        for conv_id in conversation_ids:
            try:
                conv = self.load_conversation(conv_id)
                conversations.append(conv)
            except ValueError as e:
                print(f"⚠️  Warning: {e}")
                continue

        return conversations

    def list_all_conversations(self) -> List[str]:
        """
        모든 대화 ID 조회 (최근순)

        Redis sorted set "ai:conversations"에서 조회

        Returns:
            대화 ID 리스트 (최근순 정렬)
        """
        try:
            # 1) legacy index 우선
            conv_ids = self.redis_client.zrevrange(
                self.LEGACY_CONVERSATION_INDEX, 0, -1
            )
            if conv_ids:
                return [str(cid) for cid in conv_ids]

            # 2) thread cache fallback
            return self._list_thread_ids_from_cache()
        except redis.RedisError as e:
            print(f"⚠️  Warning: Redis error while listing conversations: {str(e)}")
            return []

    def _list_thread_ids_from_cache(self) -> List[str]:
        """ai:thread:* 캐시 키에서 thread_id 목록을 최근순으로 반환."""
        items: list[tuple[str, float]] = []
        pattern = f"{self.THREAD_CACHE_PREFIX}*"

        for key in self.redis_client.scan_iter(match=pattern):
            thread_id = str(key).replace(self.THREAD_CACHE_PREFIX, "", 1)
            if not thread_id:
                continue

            # updated_at 기반 정렬을 위해 payload 조회
            updated_at = 0.0
            raw = self.redis_client.get(str(key))
            if raw:
                try:
                    payload = json.loads(raw)
                    updated_at = float(payload.get("updated_at", 0.0) or 0.0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    updated_at = 0.0

            items.append((thread_id, updated_at))

        items.sort(key=lambda x: x[1], reverse=True)
        return [thread_id for thread_id, _ in items]

    def _load_thread_from_cache(self, thread_id: str) -> ConversationData | None:
        """ai:thread:{thread_id} 캐시를 ConversationData로 변환."""
        key = f"{self.THREAD_CACHE_PREFIX}{thread_id}"
        raw = self.redis_client.get(key)
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        messages_raw = data.get("messages", [])
        messages: list[dict[str, Any]] = []
        for msg in messages_raw:
            if not isinstance(msg, dict):
                continue
            messages.append(
                {
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                    "metadata": msg.get("metadata", {}) or {},
                    "timestamp": float(msg.get("timestamp", 0.0) or 0.0),
                    "status": msg.get("status", "completed"),
                }
            )

        return ConversationData(
            conversation_id=str(data.get("thread_id") or thread_id),
            title=str(data.get("title") or "새 대화"),
            messages=messages,
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            metadata=data.get("metadata", {}) or {},
        )
