"""Redis Pub/Sub 리스너.

Redis Pub/Sub 채널을 구독하고 메시지를 WebSocket으로 브로드캐스트합니다.
"""
import asyncio
import json
from typing import Optional

from redis.asyncio import Redis

from ..core.config import get_settings
from ..core.logging import logger
from .connection_manager import ConnectionManager

settings = get_settings()


class RedisListener:
    """Redis Pub/Sub 리스너.
    
    Redis 채널 패턴을 구독하고 수신한 메시지를
    ConnectionManager를 통해 WebSocket 클라이언트에게 전달합니다.
    """

    def __init__(self, connection_manager: ConnectionManager):
        """초기화.

        Args:
            connection_manager: WebSocket 연결 관리자
        """
        self.manager = connection_manager
        self.redis: Optional[Redis] = None
        self.pubsub = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, pattern: str = "events:*") -> None:
        """Redis 리스너를 시작합니다.

        Args:
            pattern: 구독할 채널 패턴 (기본: "events:*")
        """
        if self._running:
            logger.warning("[RedisListener] Already running")
            return

        try:
            # Redis 연결 생성 (비동기)
            self.redis = Redis.from_url(
                settings.redis_url, decode_responses=True, encoding="utf-8"
            )
            self.pubsub = self.redis.pubsub()

            # 패턴 구독
            await self.pubsub.psubscribe(pattern)
            logger.info("[RedisListener] Subscribed to pattern: {}", pattern)

            self._running = True

            # 백그라운드 태스크로 리스닝 시작
            self._task = asyncio.create_task(self._listen())
            logger.info("[RedisListener] Started listening")

        except Exception as exc:
            logger.exception("[RedisListener] Failed to start: {}", exc)
            await self.stop()
            raise

    async def _listen(self) -> None:
        """Redis 메시지를 수신하고 WebSocket으로 전달합니다."""
        logger.info("[RedisListener] Listening for messages...")

        try:
            async for message in self.pubsub.listen():
                if not self._running:
                    break

                # 메시지 타입 확인
                if message["type"] != "pmessage":
                    continue

                try:
                    # 채널과 데이터 추출
                    channel = message["channel"]
                    data = message["data"]

                    # JSON 파싱
                    if isinstance(data, str):
                        event = json.loads(data)
                    else:
                        event = data

                    logger.debug(
                        "[RedisListener] Received: channel={}, type={}",
                        channel,
                        event.get("type", "unknown"),
                    )

                    # ConnectionManager를 통해 브로드캐스트
                    # 1. 특정 파일 채널로 전송 (events:file_progress:{id})
                    await self.manager.broadcast_to_channel(channel, event)
                    
                    # 2. 글로벌 채널로 전송 (events:file_progress:global)
                    # 모든 파일의 진행 상황을 구독하는 클라이언트(목록 페이지)를 위함
                    await self.manager.broadcast_to_channel("events:file_progress:global", event)

                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[RedisListener] Invalid JSON from channel {}: {}",
                        channel,
                        exc,
                    )
                except Exception as exc:
                    logger.exception(
                        "[RedisListener] Error processing message: {}", exc
                    )

        except asyncio.CancelledError:
            logger.info("[RedisListener] Listening cancelled")
        except Exception as exc:
            logger.exception("[RedisListener] Error in listen loop: {}", exc)
        finally:
            logger.info("[RedisListener] Stopped listening")

    async def stop(self) -> None:
        """Redis 리스너를 중지합니다."""
        logger.info("[RedisListener] Stopping...")
        self._running = False

        # 백그라운드 태스크 취소
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Pub/Sub 연결 종료
        if self.pubsub:
            try:
                await self.pubsub.unsubscribe()
                await self.pubsub.close()
            except Exception as exc:
                logger.warning("[RedisListener] Error closing pubsub: {}", exc)

        # Redis 연결 종료
        if self.redis:
            try:
                await self.redis.close()
            except Exception as exc:
                logger.warning("[RedisListener] Error closing redis: {}", exc)

        logger.info("[RedisListener] Stopped")

    def is_running(self) -> bool:
        """리스너가 실행 중인지 확인합니다.

        Returns:
            실행 중이면 True
        """
        return self._running
