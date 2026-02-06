"""
Redis 대화 로더

Redis에서 대화 데이터를 로드하는 구현체
"""

from typing import List
import redis
import json

from ..core.interfaces import IConversationLoader, ConversationData


class RedisConversationLoader(IConversationLoader):
    """
    Redis에서 대화 로드

    Redis 키 구조:
    - ai:conversation:{conversation_id}: 대화 데이터 (JSON)
    - ai:conversations: 대화 ID 목록 (Sorted Set, score=timestamp)
    """

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        """
        Args:
            host: Redis 호스트
            port: Redis 포트
            db: Redis DB 번호
        """
        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True
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
        key = f"ai:conversation:{conversation_id}"
        data = self.redis_client.get(key)

        if not data:
            raise ValueError(f"Conversation {conversation_id} not found in Redis")

        try:
            conv_dict = json.loads(data)
            return ConversationData(**conv_dict)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid conversation data for {conversation_id}: {str(e)}")

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
            # zrevrange: score 기준 역순 (최근순)
            conv_ids = self.redis_client.zrevrange("ai:conversations", 0, -1)
            return [str(cid) for cid in conv_ids]
        except redis.RedisError as e:
            print(f"⚠️  Warning: Redis error while listing conversations: {str(e)}")
            return []
